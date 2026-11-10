"""Histórico en CSV dentro del repo: simple, diffeable y versionado por git."""
from __future__ import annotations

import csv
from pathlib import Path

from .config import ROOT
from .models import Observacion

CSV_PATH = ROOT / "data" / "observaciones.csv"
INT_COLS = {"escalas", "duracion_min", "precio", "pasajeros"}


def leer(path: Path = CSV_PATH) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    for r in filas:
        for c in INT_COLS:
            r[c] = int(r[c]) if r.get(c) not in (None, "") else 0
    return filas


def agregar(obs: list[Observacion], path: Path = CSV_PATH) -> int:
    if not obs:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not path.exists() or path.stat().st_size == 0
    cols = Observacion.columnas()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if nuevo:
            w.writeheader()
        for o in obs:
            w.writerow(o.as_row())
    return len(obs)
