"""Hub data for the BDBV (Bundibugyo Ebola, DRC) modelling hub: weekly counts, delays, pseudo-vintages.

Only 4 real data releases exist (2026-08-18, 10-09, 10-17, 10-24), too few to backtest a nowcast.
The hub also publishes every case's onset week and onset-to-report delay by province, so the
counts as they stood on any past date can be rebuilt: a case was known on day T if
onset + delay + processing lag <= T. The onset day within the week is unknown; it is drawn
from the daily onset counts of that province and week.
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd

HUB = Path(os.environ.get("BDBV_HUB", Path(__file__).resolve().parent / "hub"))
TARGET = "wk inc confirmed cases"


def load_releases() -> pd.DataFrame:
    """Every real release: as_of, target_end_date, location, location_type, observation."""
    ts = pd.read_csv(HUB / "target-data/time-series.csv", parse_dates=["as_of", "target_end_date"])
    return ts[["as_of", "target_end_date", "location", "location_type", "observation"]]


def load_latest() -> pd.DataFrame:
    releases = load_releases()
    return releases[releases.as_of == releases.as_of.max()].drop(columns="as_of")


def load_locations() -> pd.DataFrame:
    locs = pd.read_csv(HUB / "auxiliary-data/locations.csv")
    names = locs[locs.location_type == "province"].set_index("location_name").location
    locs["province"] = np.where(locs.location_type == "province", locs.location,
                                locs.parent.map(names))
    return locs


def load_case_delays(snapshot: str | None = None) -> pd.DataFrame:
    """One row per case: province code, onset week end, days from onset to report.

    `snapshot` (e.g. "2026-10-17") reads the hub's archived copy of the delay file as it stood then.
    """
    path = (HUB / "auxiliary-data/weekly-reporting-delays-by-province.csv" if snapshot is None else
            HUB / f"auxiliary-data/archived-delay-data/weekly-reporting-delays-by-province-{snapshot}.csv")
    delays = pd.read_csv(path, parse_dates=["week_ending", "as_of"])
    delays = delays[delays.as_of == delays.as_of.max()]
    codes = load_locations().query("location_type == 'province'").set_index("location_name").location
    delays["province"] = delays.location_name.map(codes)
    if delays.province.isna().any():
        missing = sorted(delays.location_name[delays.province.isna()].unique())
        raise ValueError(f"[load_case_delays] Unknown province names: {missing}")
    return delays[["province", "week_ending", "days_sym_to_report"]].reset_index(drop=True)


def load_daily_onsets() -> pd.DataFrame:
    """Latest daily onset counts per province (observed and imputed onsets summed)."""
    daily = pd.read_csv(HUB / "auxiliary-data/daily-confirmed-case.csv",
                        parse_dates=["as_of", "target_end_date"])
    daily = daily[(daily.as_of == daily.as_of.max()) & (daily.location_type == "province")]
    return (daily.groupby(["location", "target_end_date"]).observation.sum()
            .rename("cases").reset_index().rename(columns={"location": "province",
                                                           "target_end_date": "onset"}))


def case_report_dates(lag_days: float, seed: int = 0, snapshot: str | None = None) -> pd.DataFrame:
    """Cases with a sampled onset day and report day = onset + delay + processing lag."""
    rng = np.random.default_rng(seed)
    cases = load_case_delays(snapshot)
    daily = load_daily_onsets()
    daily["week_ending"] = daily.onset + pd.to_timedelta((5 - daily.onset.dt.dayofweek) % 7, "D")
    weights = daily.groupby(["province", "week_ending"])
    offsets = np.empty(len(cases))
    for (province, week), idx in cases.groupby(["province", "week_ending"]).groups.items():
        try:
            days = weights.get_group((province, week))
            p = days.cases.to_numpy(float) / days.cases.sum()
            back = (week - days.onset).dt.days.to_numpy()
        except KeyError:
            p, back = np.full(7, 1 / 7), np.arange(7)
        offsets[cases.index.get_indexer(idx)] = rng.choice(back, size=len(idx), p=p)
    cases["onset"] = cases.week_ending - pd.to_timedelta(offsets, "D")
    cases["reported"] = cases.onset + pd.to_timedelta(cases.days_sym_to_report + lag_days, "D")
    return cases


def reported_fraction(cases: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Per province and onset week: share of eventually-reported cases known by `as_of`."""
    known = cases[cases.week_ending <= as_of].assign(known=lambda d: d.reported <= as_of)
    return known.groupby(["province", "week_ending"]).known.agg(["mean", "size"]).reset_index()


def release_growth(max_age: int = 8) -> pd.DataFrame:
    """Country growth of each onset week between consecutive weekly releases, by age in weeks.

    Age k = weeks (rounded) from the week's end to the earlier release. Only pairs 6-8 days apart count.
    """
    rel = load_releases()
    country = rel[rel.location == "CD"].pivot(index="target_end_date", columns="as_of", values="observation")
    dates = sorted(country.columns)
    rows = []
    for a, b in zip(dates[:-1], dates[1:]):
        if not 6 <= (b - a).days <= 8:
            continue
        for week in country.index[country[a].notna() & country[b].notna() & (country[a] > 0)]:
            days = (a - week).days  # negative: the week hadn't ended at the earlier release
            age = round(days / 7)  # the 9 Oct release is a Friday: 6 days counts as 1 week
            if days >= 0 and age <= max_age:
                # Per 7 days: the 9 -> 17 Oct pair spans 8 days and would overstate weekly backfill.
                growth = (country.at[week, b] / country.at[week, a]) ** (7 / (b - a).days)
                rows.append({"pair": a, "age": age, "growth": growth})
    if not rows:
        raise ValueError("[release_growth] No pair of weekly releases to learn from")
    return pd.DataFrame(rows)


def release_completeness(tail: float = 1.03, max_age: int = 8,
                         day_factor: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Chain ladder on the real releases: completeness by age 0..max_age, and its log s.d.

    F(k) = 1 / (prod_{j >= k} g_j * tail), g_j = median growth at age j over release pairs.
    The delay file misses the late backfill (weeks 2-4 old grew 6-14% between the 17 and 24 Oct
    releases, it predicted 0-5%), so this sets the level and the delay file only the province pattern.
    Median, not geometric mean: with 3 pairs the mean kept the 17 -> 24 Oct
    batch, the unusual one, and the pre-registered level test showed the 31 Oct round ~8-10% high.
    `tail`: growth after max_age weeks (weeks 5-12 old grew 3-8% from Aug to Oct).
    `day_factor`: extra completeness of ages 0, 1, ... when the release is dated after the Saturday
    its newest week ends on (bdbv_model.release_day_factor).
    The log s.d. is a rule, 0.05 + half the correction, capped at 0.5: the spread across 3 pairs
    can't be estimated well (one pair covers the newest week), and uncapped the rule gave 95%
    intervals of 200-2,700 for a ~500 week.
    """
    growth = release_growth(max_age)
    log_g = np.log(growth.groupby(["age", "pair"]).growth.mean()).unstack("pair").reindex(range(max_age + 1))
    median_log = log_g.median(axis=1).fillna(0.0).to_numpy()
    remaining = np.cumsum(median_log[::-1])[::-1] + np.log(tail)
    if day_factor is not None:
        remaining[: len(day_factor)] -= np.log(day_factor)
    remaining = np.maximum(remaining, 0.0)
    return np.exp(-remaining), np.minimum(0.05 + remaining / 2, 0.5)


def pseudo_vintage(latest: pd.DataFrame, cases: pd.DataFrame, as_of: pd.Timestamp,
                   seed: int = 0) -> pd.DataFrame:
    """Counts as they would have stood on `as_of`, for every location.

    Provinces and the country use the line list directly, scaled to the latest counts (the line
    list misses some cases). Health zones are thinned binomially with their province's fraction,
    since delays aren't published per zone.
    """
    rng = np.random.default_rng(seed)
    frac = reported_fraction(cases, as_of).set_index(["province", "week_ending"])["mean"]
    locs = load_locations().set_index("location")
    data = latest[latest.target_end_date <= as_of].copy()
    data["province"] = data.location.map(locs.province)
    key = pd.MultiIndex.from_arrays([data.province, data.target_end_date])
    # Weeks with no line-list cases in a province: fall back to the country-wide fraction.
    country = reported_fraction(cases.assign(province="CD"), as_of).set_index("week_ending")["mean"]
    f = pd.Series(frac.reindex(key).to_numpy(), index=data.index)
    f = f.fillna(data.target_end_date.map(country)).fillna(1.0).clip(0, 1)
    data["observation"] = np.where(
        data.location_type == "health zone",
        rng.binomial(data.observation.astype(int), f),
        np.round(data.observation * f))
    is_country = data.location == "CD"
    provinces = data[data.location_type == "province"].groupby("target_end_date").observation.sum()
    data.loc[is_country, "observation"] = data.loc[is_country, "target_end_date"].map(provinces)
    return data.drop(columns="province").assign(as_of=as_of)
