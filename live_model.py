"""Live model v2: nowcast every health zone, forecast zones with the North Kivu-tested ensemble
(renewal and spatial parts damped toward R = 1, plus persistence), and sum zone draws to provinces
and the country. A country stack with national models was tried and lost to the plain sum once the
spatial part was damped.

Zones: every zone of a province that has had a case (178 on 24 Oct), so spread into quiet zones
reaches the province and country totals. Quiet zones (no case in the nowcast window) aren't
nowcast: there is nothing to scale up, and the floor in `nowcast` would invent cases.
Same output and call shape as bdbv_model.forecast, so both run in the same harnesses.
"""
from dataclasses import replace

import numpy as np
import pandas as pd

from bdbv_data import load_locations
from bdbv_model import N_SAMPLES, NOWCAST_WEEKS, Params, completeness, delay_cdfs, nowcast
from context import inrb_names, population, travel_weights
from zone_models import GI, ensemble

HISTORY_WEEKS = 20  # enough for the serial interval and the spatial model's 8-week window
DAMPED = replace(Params(), r_damping=0.7)
TREND_SD = 0.2


def zone_frame(vintage: pd.DataFrame) -> pd.DataFrame:
    locs = load_locations()
    zones = locs[locs.location_type == "health zone"]
    with_cases = vintage[(vintage.location_type == "health zone") & (vintage.observation > 0)].location
    provinces = set(zones.set_index("location").province[with_cases.unique()])
    return zones[zones.province.isin(provinces)].reset_index(drop=True)


def nowcast_zones(counts: np.ndarray, zone_province: np.ndarray, weeks: pd.DatetimeIndex, as_of: pd.Timestamp,
                  cdfs: dict[str, np.ndarray], level_scale: np.ndarray | None, p: Params,
                  rng: np.random.Generator) -> np.ndarray:
    """Samples x weeks x zones of final counts."""
    paths = np.tile(counts, (N_SAMPLES, 1, 1)).astype(float)
    start = len(weeks) - NOWCAST_WEEKS - len(GI)
    for z in range(counts.shape[1]):
        if counts[start:, z].sum() == 0:
            continue
        frac = completeness(weeks, as_of, cdfs.get(zone_province[z], cdfs["CD"]))
        if level_scale is not None:
            k = level_scale.shape[1]
            frac[:, -k:] = (frac[:, -k:] * level_scale).clip(1e-3, 1)
        paths[:, start:, z] = nowcast(counts[start:, z], frac[:, start:], p, rng)
    return paths


def forecast_zones(vintage: pd.DataFrame, cases: pd.DataFrame, as_of: pd.Timestamp, locations: list[str],
                   horizon: int, p: Params = Params(), seed: int = 0,
                   release_level: tuple[np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    if as_of.day_of_week != 5:
        raise ValueError(f"[forecast_zones] as_of {as_of.date()} is not a Saturday; weeks end on Saturdays")
    rng = np.random.default_rng(seed)
    zones = zone_frame(vintage)
    codes, names, zone_province = zones.location.to_numpy(), zones.location_name.tolist(), zones.province.to_numpy()
    weeks = pd.date_range(end=as_of, periods=HISTORY_WEEKS, freq="7D")
    counts = (vintage[vintage.location.isin(codes)]
              .pivot_table(index="target_end_date", columns="location", values="observation", aggfunc="sum")
              .reindex(index=weeks, columns=codes).fillna(0).to_numpy())
    cdfs = delay_cdfs(cases, as_of, p, rng)
    level_scale = None
    if release_level is not None:
        f_real, sd = release_level[0][::-1], release_level[1][::-1]
        f_delay = completeness(weeks[-len(f_real):], as_of, cdfs["CD"]).mean(axis=0)
        shock = rng.normal(size=(N_SAMPLES, 1))  # shared by weeks and zones: backfill comes in batches
        level_scale = np.exp(np.log(f_real / f_delay)[None] + shock * sd[None])
    paths = nowcast_zones(counts, zone_province, weeks, as_of, cdfs, level_scale, p, rng)
    inrb = inrb_names(list(codes), names)
    # trend_sd 0.2 : at 0.1 aggregate 90% coverage fell to 0.77 at 4 weeks; 0.2 gives
    # 0.81-0.90 with log-CRPS unchanged. With both parts damped, prequential run: zones 0.863,
    # provinces 0.914, country 0.956 of persistence (undamped: 0.869, 0.931, 1.001).
    ahead = ensemble(paths, horizon, rng, zone_province, travel_weights(inrb), population(inrb), trend_sd=TREND_SD,
                     renewal=DAMPED)
    values = np.concatenate([paths[:, -NOWCAST_WEEKS:], ahead], axis=1)  # samples x weeks x zones
    dates = list(weeks[-NOWCAST_WEEKS:]) + list(pd.date_range(as_of + pd.Timedelta(weeks=1), periods=horizon,
                                                              freq="7D"))
    series = {code: values[:, :, z] for z, code in enumerate(codes)}
    for prov in np.unique(zone_province):
        series[prov] = values[:, :, zone_province == prov].sum(axis=2)
    series["CD"] = values.sum(axis=2)
    rows = []
    for loc in locations:
        v = series.get(loc, np.zeros((N_SAMPLES, len(dates))))
        for j, date in enumerate(dates):
            rows.append(pd.DataFrame({"location": loc, "target_end_date": date,
                                      "sample": np.arange(1, N_SAMPLES + 1), "value": v[:, j]}))
    return pd.concat(rows, ignore_index=True)
