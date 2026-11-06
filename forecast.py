"""Build the pedro-zonemix file for one round from the latest hub release, and check it.

The release dated T feeds the round whose reference_date is T + 7 days (submitted Sun-Tue of that
week). With STALE_OK=1 an older or partial release is used, with a longer horizon if it is older, so
a round is never skipped.
forecast_date is today in Kinshasa: the hub accepts it within one day of the validation date.

Exit 3: the round's release isn't fully out yet. Exit 4: the hub hasn't opened the round in tasks.json.

Usage: [BDBV_HUB=hub] [STALE_OK=1] python forecast.py <reference_date> <out_dir>
"""
import json
import os
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from bdbv_data import HUB, TARGET, case_report_dates, load_releases, release_completeness
from bdbv_model import N_SAMPLES, Params, release_day_factor
from live_model import forecast_zones

MODEL = "pedro-zonemix"
ACTIVE_WEEKS = 8
EXIT_NO_DATA, EXIT_NO_ROUND = 3, 4


class NotReady(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def round_tasks(reference_date: pd.Timestamp) -> dict:
    rounds = json.loads((HUB / "hub-config/tasks.json").read_text())["rounds"]
    for r in rounds:
        task_ids = r["model_tasks"][0]["task_ids"]
        dates = set(task_ids["reference_date"]["optional"] or []) | set(task_ids["reference_date"]["required"] or [])
        if reference_date.date().isoformat() in dates:
            return task_ids
    raise NotReady(f"[forecast] Round {reference_date.date()} is not in the hub's tasks.json yet", EXIT_NO_ROUND)


def active_locations(vintage: pd.DataFrame, as_of: pd.Timestamp) -> list[str]:
    recent = vintage[vintage.target_end_date > as_of - pd.Timedelta(weeks=ACTIVE_WEEKS)]
    sums = recent.groupby("location").observation.sum()
    return sorted(set(sums[sums > 0].index) | {"CD"})


def aux_release_dates() -> set[pd.Timestamp]:
    """Newest as_of of each auxiliary file the nowcast reads; they must match the release."""
    files = ["weekly-reporting-delays-by-province.csv", "daily-confirmed-case.csv"]
    return {pd.to_datetime(pd.read_csv(HUB / "auxiliary-data" / f, usecols=["as_of"]).as_of).max() for f in files}


def build(reference_date: pd.Timestamp) -> pd.DataFrame:
    releases = load_releases()
    released = releases.as_of.max()
    # Weeks end on Saturdays; a release dated later (e.g. a Sunday) holds weeks up to the Saturday before.
    as_of = released - pd.Timedelta(days=(released.day_of_week - 5) % 7)
    gap = reference_date - as_of
    if gap < pd.Timedelta(days=7):
        raise ValueError(f"[build] Release {as_of.date()} is newer than round {reference_date.date()} expects")
    expected = reference_date - pd.Timedelta(days=7)
    # A release dated the Friday before still counts as this week's (2026-10-09 was one).
    current = released >= expected - pd.Timedelta(days=1)
    aux = aux_release_dates()
    if not (current and aux == {released}) and not os.environ.get("STALE_OK"):
        raise NotReady(f"[build] Round {reference_date.date()} needs a release from {expected.date()} with "
                       f"matching auxiliary files; release {released.date()}, auxiliary "
                       f"{sorted(d.date().isoformat() for d in aux)}", EXIT_NO_DATA)
    vintage = releases[releases.as_of == released].drop(columns="as_of")
    p = replace(Params(), lag_days=0.0)  # the delay file is the same snapshot as the release
    cases = case_report_dates(p.lag_days)
    locations = active_locations(vintage, as_of)
    horizon = gap.days // 7 + 3  # up to reference_date + 3 weeks, the hub's last target week
    day_factor = release_day_factor(cases, as_of, released, p)
    level = release_completeness(day_factor=day_factor)
    print(f"release {released.date()} for weeks to {as_of.date()}; day factor {day_factor.round(2)}; "
          f"completeness by age: {level[0].round(2)}")
    samples = forecast_zones(vintage, cases, as_of, locations, horizon, p,
                             seed=int(as_of.strftime("%Y%m%d")), release_level=level)
    today = datetime.now(ZoneInfo("Africa/Kinshasa")).date()
    return pd.DataFrame({
        "reference_date": reference_date.date().isoformat(),
        "forecast_date": min(today, reference_date.date()).isoformat(),
        "target": TARGET,
        "target_end_date": samples.target_end_date.dt.date.astype(str),
        "location": samples.location,
        "output_type": "sample",
        "output_type_id": samples["sample"],
        "value": samples.value.round().astype(int),
    })


def check(sub: pd.DataFrame, task_ids: dict) -> None:
    """The hub validations that can fail on this file, so a broken PR never gets opened."""
    problems = []
    for column in ["reference_date", "location", "target_end_date", "target"]:
        allowed = set(task_ids[column]["optional"] or []) | set(task_ids[column]["required"] or [])
        bad = set(sub[column]) - allowed
        if bad:
            problems.append(f"{column} not accepted: {sorted(bad)[:5]}")
    per_location = sub.groupby("location").output_type_id.nunique()
    if (per_location != N_SAMPLES).any():
        problems.append(f"locations without {N_SAMPLES} samples: {list(per_location[per_location != N_SAMPLES].index)}")
    weeks = sub.groupby(["location", "output_type_id"]).target_end_date.nunique()
    if weeks.nunique() != 1:
        problems.append("trajectories cover different target_end_dates")
    if sub.forecast_date.nunique() != 1 or (sub.forecast_date > sub.reference_date).any():
        problems.append("forecast_date must be one date, on or before reference_date")
    if (sub.value < 0).any() or sub.isna().any().any():
        problems.append("negative or missing values")
    if problems:
        raise ValueError("[check] Submission invalid: " + "; ".join(problems))


def main() -> None:
    reference_date, out = pd.Timestamp(sys.argv[1]), Path(sys.argv[2])
    try:
        task_ids = round_tasks(reference_date)
        sub = build(reference_date)
    except NotReady as e:
        print(e)
        sys.exit(e.code)
    check(sub, task_ids)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{reference_date.date()}-{MODEL}.csv"
    sub.to_csv(path, index=False)
    print(f"{path}: {sub.location.nunique()} locations, {len(sub)} rows")
    country = sub[sub.location == "CD"].groupby("target_end_date").value.quantile([.05, .5, .95]).unstack()
    print(country.round())


if __name__ == "__main__":
    main()
