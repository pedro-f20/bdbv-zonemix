"""Delay-adjusted nowcast + renewal forecast of weekly confirmed BDBV cases, by location.

1. Completeness: share of a week's cases known by the data date. Delays shortened as the response
   grew, so they're estimated from the last 4 weeks of onsets with a reverse-time hazard (handles
   right truncation; chain ladder by day), per province shrunk toward national, as Beta draws so
   each trajectory carries its own completeness.
2. Nowcast: week by week, oldest first, each trajectory's true count is
   reported + Poisson((1 - F) * lambda), lambda ~ Gamma posterior given the reported count and a
   prior centred on that trajectory's previous week.
3. Forecast: weekly renewal I_t ~ NegBin(R_t * sum_k w_k I_{t-k}). R is estimated per trajectory
   from the last weeks (Cori et al. conjugate Gamma); health zones' prior is their province's R,
   provinces' the country's. log R does a random walk over the forecast weeks.
4. Half the trajectories instead carry the last nowcasted week forward (random walk). On the dev
   backtest the renewal ran high and the flat path low, about equally.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from bdbv_data import load_locations

N_SAMPLES = 100
MAX_DELAY = 60  # days; 99% of cases are reported within 33
NOWCAST_WEEKS = 5


@dataclass(frozen=True)
class Params:
    lag_days: float = 3.0
    delay_window_days: int = 28  # onsets used to estimate each delay's reverse hazard
    delay_shrink: float = 20.0  # national pseudo-cases mixed into each province's hazard
    prior_shape: float = 10.0  # how strongly a week's nowcast is tied to the previous week (dev: 10 beat 3)
    r_window: int = 3  # weeks used to estimate R
    r_prior_shape: float = 20.0  # pooling of zone R toward province R (dev: 20 beat 5)
    flat_share: float = 0.5  # trajectories that carry the level forward instead of the renewal
    r_walk_sd: float = 0.1  # weekly s.d. of log R over the forecast
    r_damping: float = 1.0  # < 1: log R shrinks toward 0 by this factor per week (see project)
    nb_size: float = 20.0  # NegBin dispersion of forecast weeks
    si_mean: float = 15.4  # Ebola serial interval, days (WHO Ebola Response Team 2014)
    si_sd: float = 9.3


def weekly_generation_weights(p: Params, max_weeks: int = 5) -> np.ndarray:
    """w[k-1] = share of the serial interval falling k weeks after the infector's onset week."""
    shape = (p.si_mean / p.si_sd) ** 2
    dist = stats.gamma(shape, scale=p.si_mean / shape)
    edges = (np.arange(max_weeks + 1) + 0.5) * 7
    w = np.diff(dist.cdf(edges))
    w[0] += dist.cdf(edges[0])
    return w / w.sum()


def reverse_hazard_counts(days_ago: np.ndarray, delay: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    """For each delay d: cases with delay == d and delay <= d among onsets d..d+window days ago.

    Every case in that onset window with delay <= d is already reported, so the ratio
    P(D = d | D <= d) is free of right truncation and only uses recent onsets.
    """
    d = np.arange(MAX_DELAY + 1)[:, None]
    in_window = (days_ago[None, :] >= d) & (days_ago[None, :] <= d + window) & (delay[None, :] <= d)
    return (in_window & (delay[None, :] == d)).sum(axis=1), in_window.sum(axis=1)


def cdf_draws(events: np.ndarray, at_risk: np.ndarray, prior: np.ndarray, weight: float,
              rng: np.random.Generator) -> np.ndarray:
    """Samples x (MAX_DELAY + 1) draws of P(D <= d), from Beta draws of the reverse hazard."""
    # A flat prior on 60 sparse daily hazards adds up to fake tail mass; the prior is informative.
    eps = 1e-3
    g = rng.beta(events + weight * prior + eps, at_risk - events + weight * (1 - prior) + eps,
                 size=(N_SAMPLES, MAX_DELAY + 1))
    g[:, 0] = 0.0  # F(MAX_DELAY) = 1, F(d) = prod_{j > d} (1 - g_j); g_0 is unused
    survive = np.cumprod((1 - g)[:, ::-1], axis=1)[:, ::-1]
    return np.concatenate([survive[:, 1:], np.ones((N_SAMPLES, 1))], axis=1)


def delay_cdfs(cases: pd.DataFrame, as_of: pd.Timestamp, p: Params,
               rng: np.random.Generator) -> dict[str, np.ndarray]:
    """Per province (and 'CD'): draws of P(onset-to-known delay <= d days) as known at as_of."""
    known = cases[cases.reported <= as_of]
    days_ago = (as_of - known.onset).dt.days.to_numpy()
    delay = np.ceil(known.days_sym_to_report + p.lag_days).astype(int).to_numpy()
    # Recent national hazards shrink toward the whole outbreak's; provinces' toward recent national.
    e_all, n_all = reverse_hazard_counts(days_ago, delay, window=10 ** 6)
    whole = np.divide(e_all, n_all, out=np.zeros(len(e_all)), where=n_all > 0)
    events, at_risk = reverse_hazard_counts(days_ago, delay, p.delay_window_days)
    national = (events + p.delay_shrink * whole) / (at_risk + p.delay_shrink)
    cdfs = {"CD": cdf_draws(events, at_risk, whole, p.delay_shrink, rng)}
    for province in known.province.unique():
        mask = (known.province == province).to_numpy()
        e, n = reverse_hazard_counts(days_ago[mask], delay[mask], p.delay_window_days)
        cdfs[province] = cdf_draws(e, n, national, p.delay_shrink, rng)
    return cdfs


def completeness(weeks: pd.DatetimeIndex, as_of: pd.Timestamp, cdf: np.ndarray) -> np.ndarray:
    """Samples x weeks: share of each onset week's cases known by as_of, onsets uniform in the week."""
    days_since = (as_of - weeks).days.to_numpy()[:, None] + np.arange(7)[None, :]
    known = np.where(days_since[None] < 0, 0.0, cdf[:, np.clip(days_since, 0, MAX_DELAY)])
    return known.mean(axis=2).clip(0, 1)


def release_day_factor(cases: pd.DataFrame, week_end: pd.Timestamp, released: pd.Timestamp, p: Params,
                       ages: int = 3, seed: int = 0) -> np.ndarray:
    """How much more of each young week (ages 0..ages-1) a release dated `released` holds than one
    dated `week_end`, the Saturday before. From the fitted national delay distribution, not the raw
    delay file: that file holds only cases reported by the snapshot date, so it reads 100% at release."""
    if released == week_end:
        return np.ones(ages)
    cdf = delay_cdfs(cases, released, p, np.random.default_rng(seed))["CD"]
    weeks = pd.DatetimeIndex([week_end - pd.Timedelta(weeks=k) for k in range(ages)])
    return completeness(weeks, released, cdf).mean(axis=0) / completeness(weeks, week_end, cdf).mean(axis=0)


def nowcast(reported: np.ndarray, frac: np.ndarray, p: Params, rng: np.random.Generator) -> np.ndarray:
    """Trajectories (samples x weeks) of final counts given reported counts and completeness draws."""
    samples = np.empty((N_SAMPLES, len(reported)))
    level = np.maximum(reported[0] / np.maximum(frac[:, 0], 0.05), 0.5)
    for t, (n, f) in enumerate(zip(reported, frac.T)):
        lam = rng.gamma(p.prior_shape + n, 1 / (p.prior_shape / np.maximum(level, 0.5) + f))
        samples[:, t] = n + rng.poisson((1 - f) * lam)
        level = samples[:, t]
    return samples


def estimate_r(history: np.ndarray, w: np.ndarray, prior_mean: np.ndarray, p: Params,
               rng: np.random.Generator) -> np.ndarray:
    """Cori-style posterior draw of R per trajectory from the last r_window weeks."""
    k = len(w)
    padded = np.concatenate([np.zeros((history.shape[0], k)), history], axis=1)
    weeks = range(padded.shape[1] - p.r_window, padded.shape[1])
    infectiousness = sum(padded[:, t - k:t][:, ::-1] @ w for t in weeks)
    cases = padded[:, -p.r_window:].sum(axis=1)
    shape = p.r_prior_shape + cases
    rate = p.r_prior_shape / np.maximum(prior_mean, 0.05) + infectiousness
    return rng.gamma(shape, 1 / rate)


def project(history: np.ndarray, r: np.ndarray, horizon: int, p: Params,
            rng: np.random.Generator) -> np.ndarray:
    """Weekly renewal forward from `history` with R drawn per trajectory.

    p.r_damping < 1: log R decays toward 0 (R toward 1) by that factor each week, and the walk is
    Jensen-corrected (mean-one multipliers). Undamped, per-zone growth compounded upward: the
    renewal part's country total ran +0.10 to +0.29 high in log terms at 1-4 weeks (North Kivu).
    """
    w = weekly_generation_weights(p)
    path = history.copy()
    out = np.empty((history.shape[0], horizon))
    log_r0 = np.log(np.maximum(r, 1e-6))
    shock = np.zeros_like(log_r0)
    for h in range(horizon):
        shock = shock + rng.normal(0, p.r_walk_sd, size=r.shape)
        if p.r_damping < 1:
            r = np.exp(log_r0 * p.r_damping ** (h + 1) + shock - (h + 1) * p.r_walk_sd ** 2 / 2)
        else:
            r = np.exp(log_r0 + shock)
        k = min(len(w), path.shape[1])
        mean = r * (path[:, -k:][:, ::-1] @ w[:k])
        out[:, h] = rng.negative_binomial(p.nb_size, p.nb_size / (p.nb_size + np.maximum(mean, 1e-9)))
        path = np.concatenate([path, out[:, h:h + 1]], axis=1)
    return out


def project_flat(history: np.ndarray, horizon: int, p: Params, rng: np.random.Generator) -> np.ndarray:
    """Last nowcasted week carried forward, with a log random walk."""
    walk = np.exp(np.cumsum(rng.normal(0, p.r_walk_sd, size=(history.shape[0], horizon)), axis=1))
    mean = history[:, -1:] * walk
    return rng.negative_binomial(p.nb_size, p.nb_size / (p.nb_size + np.maximum(mean, 1e-9)))


def forecast(vintage: pd.DataFrame, cases: pd.DataFrame, as_of: pd.Timestamp, locations: list[str],
             horizon: int, p: Params = Params(), seed: int = 0,
             release_level: tuple[np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    """Sample trajectories: location, target_end_date, sample, value.

    Covers the NOWCAST_WEEKS onset weeks up to as_of and `horizon` weeks after it.
    Locations are processed country -> provinces -> zones so each level's R is the next one's prior.
    `release_level` (live only): completeness by age and its log s.d. from the real releases
    (bdbv_data.release_completeness); the delay file then sets only the province pattern.
    """
    if as_of.day_of_week != 5:
        raise ValueError(f"[forecast] as_of {as_of.date()} is not a Saturday; weeks end on Saturdays")
    rng = np.random.default_rng(seed)
    w = weekly_generation_weights(p)
    locs = load_locations().set_index("location")
    cdfs = delay_cdfs(cases, as_of, p, rng)
    counts = vintage.pivot_table(index="location", columns="target_end_date", values="observation",
                                 aggfunc="sum").fillna(0)
    weeks = pd.date_range(counts.columns.min(), as_of, freq="7D")
    counts = counts.reindex(columns=weeks, fill_value=0)
    if release_level is not None:
        f_real, sd = release_level[0][::-1], release_level[1][::-1]  # oldest age first, like weeks
        f_delay = completeness(weeks[-len(f_real):], as_of, cdfs["CD"]).mean(axis=0)
        # One shock per trajectory, shared by all weeks and locations: backfill arrives in batches.
        shock = rng.normal(size=(N_SAMPLES, 1))
        level_scale = np.exp(np.log(f_real / f_delay)[None] + shock * sd[None])
    level = {"country": 0, "province": 1, "health zone": 2}
    order = sorted(locations, key=lambda loc: level[locs.location_type[loc]])
    r_draws: dict[str, np.ndarray] = {}
    rows = []
    future = pd.date_range(as_of + pd.Timedelta(weeks=1), periods=horizon, freq="7D")
    for loc in order:
        series = counts.loc[loc].to_numpy() if loc in counts.index else np.zeros(len(weeks))
        cdf = cdfs.get(locs.province[loc] if loc != "CD" else "CD", cdfs["CD"])
        frac = completeness(weeks, as_of, cdf)
        if release_level is not None:
            k = level_scale.shape[1]
            frac[:, -k:] = (frac[:, -k:] * level_scale).clip(1e-3, 1)
        # Nowcast a few weeks before those submitted, so R sees nowcasted infectiousness too.
        start = max(len(weeks) - NOWCAST_WEEKS - len(w), 0)
        history = np.tile(series, (N_SAMPLES, 1)).astype(float)
        history[:, start:] = nowcast(series[start:], frac[:, start:], p, rng)
        parent = "CD" if locs.location_type[loc] == "province" else locs.province.get(loc)
        prior = r_draws.get(parent, np.ones(N_SAMPLES))
        r = estimate_r(history, w, prior, p, rng)
        r_draws[loc] = r
        ahead = project(history, r, horizon, p, rng)
        n_flat = round(p.flat_share * N_SAMPLES)
        ahead[:n_flat] = project_flat(history[:n_flat], horizon, p, rng)
        values = np.concatenate([history[:, -NOWCAST_WEEKS:], ahead], axis=1)
        dates = list(weeks[-NOWCAST_WEEKS:]) + list(future)
        for j, date in enumerate(dates):
            rows.append(pd.DataFrame({"location": loc, "target_end_date": date,
                                      "sample": np.arange(1, N_SAMPLES + 1), "value": values[:, j]}))
    return pd.concat(rows, ignore_index=True)
