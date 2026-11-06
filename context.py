"""Road travel times and populations by health zone, from INRB's public context data.

Copied into data/ from https://github.com/inrb-umie/BDBV2026-Data (MIT), commit a5ee90c:
build/matrix/osrm__travel_time__static.matrix.csv and build/long/worldpop__pop_count.csv.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
# Hub zones that INRB's data name differently (Bili and Lubunga exist in two provinces).
INRB_BY_CODE = {"CD-BU-03": "Bili (Bas-Uele)", "CD-NU-02": "Bili (Nord-Ubangi)",
                "CD-KC-14": "Lubunga (Kasaï-Central)", "CD-TO-10": "Lubunga (Tshopo)"}
INRB_BY_NAME = {"Gethy": "Gety", "Hauts Plateaux": "Haut Plateau", "Bogosenubia": "Bogosenubea"}


def inrb_names(codes: list[str], names: list[str]) -> list[str]:
    """Hub zone code + name -> the name INRB's matrices and population file use."""
    return [INRB_BY_CODE.get(c, INRB_BY_NAME.get(n, n)) for c, n in zip(codes, names)]


def travel_weights(names: list[str], kind: str = "osrm__travel_time") -> np.ndarray:
    """Zones x zones matrix, rows/columns in `names` order (INRB names)."""
    m = pd.read_csv(DATA / f"{kind}__static.matrix.csv", index_col=0)
    m.index = m.index.str.strip().str.lstrip("﻿")
    missing = sorted(set(names) - set(m.index))
    if missing:
        raise ValueError(f"[travel_weights] Zones missing from {kind}: {missing}")
    return m.loc[names, names].to_numpy(float)


def population(names: list[str]) -> np.ndarray:
    pop = pd.read_csv(DATA / "worldpop__pop_count.csv", header=None, names=["zone", "pop"],
                      encoding="utf-8-sig").set_index("zone")["pop"]
    missing = sorted(set(names) - set(pop.index))
    if missing:
        raise ValueError(f"[population] Zones missing: {missing}")
    return pop.loc[names].to_numpy(float)
