"""Zone-level forecast models on weekly counts, for any outbreak.

History is weeks x zones (fully observed, e.g. North Kivu 2018-20) or samples x weeks x zones
(one nowcasted history per trajectory, live). Each model returns N_SAMPLES x horizon x zones;
provinces and the country are sums of zone draws, so the levels add up.

- persist: last week's count, NegBin noise.
- renewal_pooled: the first live model per zone (Cori R pooled toward the province R, 50/50 with
  a flat path).
- spatial: endemic-epidemic model (Held, Höhle & Hofmann 2005; Meyer & Held 2014), with
  serial-interval-weighted infectiousness instead of last week's count:
      mu_it = lambda_i * L_it + phi * sum_j W_ij L_jt + nu * pop_i / mean(pop)
  lambda_i = exp(a + u_i) with a ridge penalty on u; W_ij from travel time, (1 + tt_ij)^-gamma,
  normalised over destinations i. Point estimate by maximum likelihood on the last `window` weeks
  of the median history; uncertainty from a log random walk on lambda.
- ensemble: equal thirds of renewal_pooled, spatial and persist (NegBin size 5), plus a shared
  trend. Evidence and its limits are in ../notes.md: forecast part only, on a declining, low-count
  outbreak with no reporting delay; at peak-like volume the total-level gain over persistence was nil.
"""
from dataclasses import dataclass

import numpy as np
from scipy import optimize, special

from bdbv_model import N_SAMPLES, Params, estimate_r, project, project_flat, weekly_generation_weights

GI = weekly_generation_weights(Params())


def as_samples(history: np.ndarray) -> np.ndarray:
    return history if history.ndim == 3 else np.tile(history, (N_SAMPLES, 1, 1)).astype(float)


def infectiousness(y: np.ndarray) -> np.ndarray:
    """L[t] = sum_k w_k y[t-k], for t = 0..T (the last row is the week after the data)."""
    k = len(GI)
    padded = np.concatenate([np.zeros((k,) + y.shape[1:]), y])
    return np.stack([np.tensordot(GI, padded[t:t + k][::-1], axes=1) for t in range(len(y) + 1)])


def nb_sample(rng: np.random.Generator, mean: np.ndarray, size: float | np.ndarray) -> np.ndarray:
    return rng.negative_binomial(size, size / (size + np.maximum(mean, 1e-9)))


def persist(history: np.ndarray, horizon: int, rng: np.random.Generator, size: float = 20.0) -> np.ndarray:
    paths = as_samples(history)
    mean = np.repeat(paths[:, -1:], horizon, axis=1)
    return nb_sample(rng, mean, size).astype(float)


def renewal_pooled(history: np.ndarray, horizon: int, rng: np.random.Generator, province: np.ndarray,
                   p: Params = Params()) -> np.ndarray:
    """Zone R prior = its province's R draws, province's = the country's."""
    paths = as_samples(history)
    out = np.zeros((N_SAMPLES, horizon, paths.shape[2]))
    n_flat = round(p.flat_share * N_SAMPLES)
    r_country = estimate_r(paths.sum(axis=2), GI, np.ones(N_SAMPLES), p, rng)
    for prov in np.unique(province):
        cols = np.flatnonzero(province == prov)
        r_prov = estimate_r(paths[:, :, cols].sum(axis=2), GI, r_country, p, rng)
        for z in cols:
            hist = paths[:, :, z]
            if hist[:, -len(GI):].sum() == 0:
                continue  # no recent cases: this model can't seed a zone
            r = estimate_r(hist, GI, r_prov, p, rng)
            ahead = project(hist, r, horizon, p, rng)
            ahead[:n_flat] = project_flat(hist[:n_flat], horizon, p, rng)
            out[:, :, z] = ahead
    return out


@dataclass(frozen=True)
class SpatialParams:
    window: int = 8  # weeks of data the likelihood uses
    # Strong pooling: with ridge 1 zone growth rates spread 0.8-1.7 live and the fastest compounded
    # (95% bounds of 300+ for ~10-a-week zones). On North Kivu dev 1, 5, 20, 100 score within 1-2%.
    ridge: float = 100.0  # penalty on zone-specific log lambda
    walk_sd: float = 0.1  # weekly s.d. of log lambda over the forecast
    gamma: float = 3.0  # travel-time decay of the import kernel (dev: 3 beat 1, 1.5 and 5)
    damping: float = 0.7  # log lambda shrinks by this per forecast week, as in the renewal; 1 = off


def import_kernel(travel_time: np.ndarray, gamma: float) -> np.ndarray:
    # No road route (NaN in OSRM) counts as very far; a zone with no routes at all (Idjwi, an island)
    # exports nothing rather than an even 1/n to every zone.
    k = (1.0 + np.nan_to_num(travel_time, nan=1e4)) ** -gamma
    k[:, np.isnan(travel_time).sum(axis=0) >= len(travel_time) - 1] = 0.0
    np.fill_diagonal(k, 0.0)
    totals = k.sum(axis=0, keepdims=True)
    return np.divide(k, totals, out=np.zeros_like(k), where=totals > 0)  # exports sum to 1 per source


def _nb_loglik(y: np.ndarray, mu: np.ndarray, size: float) -> float:
    mu = np.maximum(mu, 1e-9)
    return float(np.sum(special.gammaln(y + size) - special.gammaln(size) - special.gammaln(y + 1)
                        + size * np.log(size / (size + mu)) + y * np.log(mu / (size + mu))))


def fit_spatial(history: np.ndarray, kernel: np.ndarray, pop_share: np.ndarray, sp: SpatialParams) -> np.ndarray:
    """MAP of theta = (a, log phi, log nu, log size, u_1..u_Z) on the last `window` weeks."""
    lam_all = infectiousness(history)
    imports_all = lam_all @ kernel.T
    weeks = slice(max(len(history) - sp.window, len(GI)), len(history))
    y, lam, imp = history[weeks], lam_all[weeks], imports_all[weeks]

    def loss(theta: np.ndarray) -> float:
        a, log_phi, log_nu, log_size = theta[:4]
        u = theta[4:]
        mu = np.exp(a + u) * lam + np.exp(log_phi) * imp + np.exp(log_nu) * pop_share
        # Weak prior on log phi (sd 2 around e^-3): imports are rare, and a window of 8 weeks with
        # no new zones would otherwise push phi to zero and the model could never seed one.
        return (-_nb_loglik(y, mu, np.exp(log_size)) + sp.ridge * np.sum(u ** 2)
                + 0.5 * (log_phi + 3) ** 2 / 4)

    theta0 = np.concatenate([[0.0, -3.0, -6.0, 1.0], np.zeros(history.shape[1])])
    return optimize.minimize(loss, theta0, method="BFGS", options={"maxiter": 500, "gtol": 1e-4}).x


def spatial(history: np.ndarray, horizon: int, rng: np.random.Generator, travel_time: np.ndarray,
            pop: np.ndarray, sp: SpatialParams = SpatialParams()) -> np.ndarray:
    paths = as_samples(history)
    kernel = import_kernel(travel_time, sp.gamma)
    pop_share = pop / pop.mean()
    # Fitted once on the median history (the mean is pulled up by the skewed backfill shock); the
    # BFGS inverse Hessian gave runaway parameter draws.
    theta = fit_spatial(np.median(paths, axis=0), kernel, pop_share, sp)
    phi, nu, size = np.exp(theta[1]), np.exp(theta[2]), float(np.exp(theta[3]).clip(0.2, 100.0))
    log_lam0 = np.tile(theta[0] + theta[4:], (N_SAMPLES, 1))
    shock = np.zeros((N_SAMPLES, 1))
    out = np.empty((N_SAMPLES, horizon, paths.shape[2]))
    for h in range(horizon):
        # Damped toward 1 like the renewal (natural-scale total 1.24 -> 1.14 of persistence).
        shock = shock + rng.normal(0, sp.walk_sd, size=(N_SAMPLES, 1))
        jensen = (h + 1) * sp.walk_sd ** 2 / 2 if sp.damping < 1 else 0.0
        lam_zone = np.exp(log_lam0 * sp.damping ** (h + 1) + shock - jensen)
        k = min(len(GI), paths.shape[1])
        L = np.einsum("k,skz->sz", GI[:k], paths[:, -k:][:, ::-1])
        mu = np.minimum(lam_zone * L + phi * (L @ kernel.T) + nu * pop_share[None], 1e5)
        out[:, h] = nb_sample(rng, mu, size)
        paths = np.concatenate([paths, out[:, h:h + 1]], axis=1)
    return out


def mixture(parts: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Equal-weight mixture: each trajectory taken whole from one model."""
    which = rng.permutation(np.arange(N_SAMPLES) % len(parts))
    return np.stack([parts[m][i] for i, m in enumerate(which)])


def common_trend(samples: np.ndarray, sd: float, rng: np.random.Generator) -> np.ndarray:
    """Scale each trajectory by a log random walk shared by all zones, then round at random.

    Zone models move mostly independently, so their sums are too narrow (North Kivu total coverage
    78% for a 90% interval, BDBV country 58%); a shared trend is what the totals were missing.
    """
    if sd == 0:
        return samples
    walk = np.exp(np.cumsum(rng.normal(0, sd, size=samples.shape[:2]), axis=1))[:, :, None]
    scaled = samples * walk
    return np.floor(scaled + rng.random(scaled.shape))


def ensemble(history: np.ndarray, horizon: int, rng: np.random.Generator, province: np.ndarray,
             travel_time: np.ndarray, pop: np.ndarray, trend_sd: float = 0.0,
             sp: SpatialParams = SpatialParams(), persist_size: float = 5.0,
             renewal: Params = Params()) -> np.ndarray:
    """The candidate: equal thirds of renewal_pooled, spatial and persist, plus a shared trend."""
    parts = mixture([renewal_pooled(history, horizon, rng, province, renewal),
                     spatial(history, horizon, rng, travel_time, pop, sp),
                     persist(history, horizon, rng, persist_size)], rng)
    return common_trend(parts, trend_sd, rng)
