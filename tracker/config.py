from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Tramo:
    nombre: str
    fecha: date
    origenes: list[str]
    destinos: list[str]


@dataclass(frozen=True)
class Franja:
    nombre: str
    desde: int
    hasta: int


@dataclass
class Config:
    adultos: int
    moneda: str
    idioma: str
    solo_directos: bool
    tramos: list[Tramo]
    franjas: list[Franja]
    google_flights: bool
    serpapi: bool
    serpapi_max_por_dia: int
    serpapi_top_n: int
    alertas: dict
    hasta: date
    raw: dict = field(default_factory=dict, repr=False)

    def franja_de(self, hora: int) -> str:
        for f in self.franjas:
            if f.desde <= hora < f.hasta:
                return f.nombre
        return "sin_franja"


def load_config(path: str | Path = ROOT / "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    v = raw["viaje"]
    fuentes = raw.get("fuentes", {})
    sa = fuentes.get("serpapi", {})
    franjas = [Franja(f["nombre"], int(f["desde"]), int(f["hasta"])) for f in raw["franjas"]]
    _validar_franjas(franjas)
    return Config(
        adultos=int(v.get("adultos", 1)),
        moneda=v.get("moneda", "COP"),
        idioma=v.get("idioma", "es-419"),
        solo_directos=bool(v.get("solo_directos", True)),
        tramos=[
            Tramo(t["nombre"], date.fromisoformat(str(t["fecha"])), list(t["origenes"]), list(t["destinos"]))
            for t in v["tramos"]
        ],
        franjas=franjas,
        google_flights=bool(fuentes.get("google_flights", {}).get("habilitada", True)),
        serpapi=bool(sa.get("habilitada", False)),
        serpapi_max_por_dia=int(sa.get("max_ejecuciones_por_dia", 1)),
        serpapi_top_n=int(sa.get("booking_options_top_n", 2)),
        alertas=raw.get("alertas") or {},
        hasta=date.fromisoformat(str(raw.get("dejar_de_rastrear_despues_de", "2100-01-01"))),
        raw=raw,
    )


def _validar_franjas(franjas: list[Franja]) -> None:
    cubiertas = sorted((f.desde, f.hasta) for f in franjas)
    for (a0, a1), (b0, _) in zip(cubiertas, cubiertas[1:]):
        if b0 < a1:
            raise ValueError(f"Franjas solapadas: {a0}-{a1} y {b0}")
