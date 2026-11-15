"""Live scoreboard: pedro-zonemix against the hub baseline and every other model, on shared cells.

Score: CRPS of the 100 samples on log(x + 1), the scale of the North Kivu backtest. Truth is the
latest release, which revises recent onset weeks upward for weeks, so a cell counts only when its
week ended MATURE_DAYS before that release and the release came after the round. Ratio = our mean
CRPS / theirs over shared cells. respicast-chronos2's geometric mean over locations is shown too but
not used: most zones have no cases, both CRPS are near 0 there and their ratios swamp the rest.

Usage: python scoreboard.py [hub_dir]   (clones the hub there, default ./hub; writes SEASON.md, scores.json)
"""
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

UPSTREAM = "https://github.com/InsightNet-US/BDBV-Modeling-Hub.git"
MODEL = "pedro-zonemix"
BASELINE = "BDBVHub-baseline"
TARGET = "wk inc confirmed cases"
# Country counts by release: a week is ~25% in at 1 day, ~75% at 8, ~90-95% at 15, ~97% at 22
MATURE_DAYS = 14
STALE_DAYS = 16  # weekly releases: older than this means the hub stopped and the scores would mislead
CELL = ["reference_date", "target_end_date", "location"]


def fetch_hub(hub: Path) -> None:
    subprocess.run(["rm", "-rf", str(hub)], check=True)
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", UPSTREAM, str(hub)],
                   check=True)
    subprocess.run(["git", "-C", str(hub), "sparse-checkout", "set", "target-data", "model-output"], check=True)


def load_truth(hub: Path, today: pd.Timestamp) -> tuple[pd.DataFrame, pd.Timestamp]:
    ts = pd.read_csv(hub / "target-data/time-series.csv", parse_dates=["as_of", "target_end_date"])
    as_of = ts.as_of.max()
    if today - as_of > pd.Timedelta(days=STALE_DAYS):
        raise RuntimeError(f"[scoreboard] Latest hub release is {as_of.date()}: older than {STALE_DAYS} days")
    truth = ts[(ts.as_of == as_of) & (ts.target == TARGET)]
    return truth[["target_end_date", "location", "observation"]], as_of


def load_forecasts(hub: Path) -> pd.DataFrame:
    """All models' samples for every round we submitted, one row per cell, sample columns."""
    rounds = sorted(p.name[:10] for p in (hub / "model-output" / MODEL).glob(f"*-{MODEL}.csv"))
    if not rounds:
        raise RuntimeError(f"[scoreboard] No {MODEL} files in the hub")
    frames = []
    for path in sorted((hub / "model-output").glob("*/*.csv")):
        if path.name[:10] not in rounds:
            continue
        df = pd.read_csv(path, parse_dates=["reference_date", "target_end_date"])
        bad = set(df.output_type) - {"sample"} or set(df.target) - {TARGET}
        if bad:
            raise ValueError(f"[scoreboard] {path.name}: unexpected output_type/target {sorted(bad)}")
        frames.append(df.assign(model=path.parent.name))
    samples = pd.concat(frames).pivot_table(index=["model", *CELL], columns="output_type_id", values="value")
    if samples.isna().any().any():
        raise ValueError("[scoreboard] Some cells don't have the same sample ids as the others")
    return samples


def crps_samples(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Sample CRPS per row of x (cells x samples) against y: E|X - y| - E|X - X'| / 2."""
    m = x.shape[1]
    xs = np.sort(x, axis=1)
    spread = (2 * np.arange(1, m + 1) - m - 1) @ xs.T * 2 / m**2
    return np.abs(x - y[:, None]).mean(axis=1) - spread / 2


def score(samples: pd.DataFrame, truth: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Log-scale CRPS, cells x models, for mature weeks of rounds before the release."""
    cells = samples.reset_index()
    keep = (cells.target_end_date <= as_of - pd.Timedelta(days=MATURE_DAYS)) & (cells.reference_date < as_of)
    cells = cells[keep].merge(truth, on=["target_end_date", "location"])
    ids = [c for c in cells.columns if c not in ["model", *CELL, "observation"]]
    cells["crps"] = crps_samples(np.log1p(cells[ids].to_numpy(float)), np.log1p(cells.observation.to_numpy(float)))
    return cells.pivot_table(index=CELL, columns="model", values="crps")


def ratio(pair: pd.DataFrame, model: str, other: str) -> float | None:
    """Mean CRPS over the shared cells, ours / theirs."""
    theirs = pair[other].mean()
    return float(pair[model].mean() / theirs) if theirs > 0 else None


def geo_ratio(pair: pd.DataFrame, model: str, other: str) -> float | None:
    """respicast's aggregate: geometric mean over locations of mean-CRPS ratios (locations with both > 0)."""
    by_loc = pair.groupby("location")[[model, other]].mean()
    by_loc = by_loc[(by_loc[model] > 0) & (by_loc[other] > 0)]
    return float(np.exp(np.log(by_loc[model] / by_loc[other]).mean())) if len(by_loc) else None


def comparisons(crps: pd.DataFrame) -> list[dict]:
    others = [BASELINE] + sorted(c for c in crps.columns if c not in (MODEL, BASELINE))
    rows = []
    for other in others:
        pair = crps[[MODEL, other]].dropna() if {MODEL, other} <= set(crps.columns) else None
        if pair is None or pair.empty:
            rows.append({"other": other, "ratio": None, "geo": None, "cells": 0, "locations": 0, "rounds": 0})
            continue
        rows.append({"other": other, "ratio": ratio(pair, MODEL, other), "geo": geo_ratio(pair, MODEL, other),
                     "cells": len(pair), "locations": pair.index.get_level_values("location").nunique(),
                     "rounds": pair.index.get_level_values("reference_date").nunique()})
    return rows


def fmt(r: float | None) -> str:
    return "-" if r is None else f"{r:.2f}"


MAX_CARD_ROWS = 6  # AI4G's parse_scores limit


def peer_rows(rows: list[dict]) -> list[dict]:
    """Other models sharing at least half our widest overlap: the baseline is easy to beat at zone level,
    so the headline counts how many real models we beat on a comparable set of cells."""
    others = [r for r in rows if r["other"] != BASELINE and r["cells"]]
    widest = max((r["cells"] for r in others), default=0)
    return [r for r in others if r["cells"] * 2 >= widest]


def build_scores(rows: list[dict], today: str) -> dict:
    base, peers = rows[0], peer_rows(rows)
    if base["ratio"] is None and not peers:
        headline = "Live scores pending: no truth yet for our rounds"
    else:
        parts = []
        if peers:
            parts.append(f"Better than {sum(r['ratio'] < 1 for r in peers)} of {len(peers)} hub models")
        if base["ratio"] is not None:
            r = base["ratio"]
            parts.append(f"{round(abs(1 - r) * 100)}% {'less' if r < 1 else 'more'} error than the baseline")
        headline = "; ".join(parts)
    shown = ([base] if base["cells"] else []) + sorted(peers, key=lambda r: -r["cells"])
    out = [{"measure": "Us vs hub baseline" if row["other"] == BASELINE else f"Us vs {row['other']}"[:40],
            "value": fmt(row["ratio"]), "range": f"{row['cells']} cells"}
           for row in shown[:MAX_CARD_ROWS]]
    n = max(r["rounds"] for r in rows)
    return {"headline": headline, "rows": out,
            "period": f"Nov 2026 rounds, {n} scored, interim; updated {today}"}


def build_md(rows: list[dict], crps: pd.DataFrame, as_of: pd.Timestamp) -> str:
    lines = ["# Live scoreboard", "",
             f"Score: CRPS of the samples on log(x+1) (as the backtest), truth = hub release of {as_of.date()}, "
             f"onset weeks ending {MATURE_DAYS}+ days before it. Ratio = {MODEL} mean CRPS / comparator's on "
             "shared cells, below 1 means we are better; geo = respicast's geometric mean over locations.", "",
             "| comparator | ratio | geo | shared cells | locations | rounds |", "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['other']} | {fmt(row['ratio'])} | {fmt(row['geo'])} | {row['cells']} "
                     f"| {row['locations']} | {row['rounds']} |")
    if rows[0]["cells"]:
        pair = crps[[MODEL, BASELINE]].dropna()
        level = pair.index.get_level_values("location").str.count("-").map({0: "country", 1: "province", 2: "zone"})
        lines += ["", f"Vs {BASELINE}:", "", "| by | ratio | cells |", "|---|---|---|"]
        for key, g in [*pair.groupby(level="reference_date"), *pair.groupby(level)]:
            name = f"round {key.date()}" if isinstance(key, pd.Timestamp) else key
            lines.append(f"| {name} | {fmt(ratio(g, MODEL, BASELINE))} | {len(g)} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    hub = Path(sys.argv[1] if len(sys.argv) > 1 else "hub")
    today = pd.Timestamp(date.today())
    fetch_hub(hub)
    truth, as_of = load_truth(hub, today)
    crps = score(load_forecasts(hub), truth, as_of)
    rows = comparisons(crps)
    Path("SEASON.md").write_text(build_md(rows, crps, as_of))
    Path("scores.json").write_text(json.dumps(build_scores(rows, today.date().isoformat()), indent=2) + "\n")
    print(f"[scoreboard] truth as of {as_of.date()}: wrote SEASON.md and scores.json")


if __name__ == "__main__":
    main()
