"""Fuente opcional: SerpApi (Google Flights API) para ver también agencias/OTAs.

Por cada tramo hace 1 búsqueda (precios por aerolínea) y luego, para los N vuelos
más baratos, pide las "booking options": la lista de quién vende ese vuelo
(la aerolínea directa, Despegar, Expedia, Kiwi, etc.) y a qué precio.
"""
from __future__ import annotations

import logging
import os

import requests

from ..config import Config, Tramo
from ..models import Observacion
from . import ResultadoFuente

log = logging.getLogger(__name__)

FUENTE = "serpapi"
URL = "https://serpapi.com/search.json"


def _params_base(cfg: Config, api_key: str) -> dict:
    return {
        "engine": "google_flights",
        "api_key": api_key,
        "hl": "es",
        "gl": "co",
        "currency": cfg.moneda,
    }


def _busqueda(tramo: Tramo, cfg: Config, api_key: str) -> dict:
    params = _params_base(cfg, api_key) | {
        "type": 2,  # solo ida
        "departure_id": ",".join(tramo.origenes),
        "arrival_id": ",".join(tramo.destinos),
        "outbound_date": tramo.fecha.isoformat(),
        "adults": cfg.adultos,
        "stops": 1 if cfg.solo_directos else 0,
    }
    r = requests.get(URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _booking_options(token: str, cfg: Config, api_key: str) -> dict:
    r = requests.get(URL, params=_params_base(cfg, api_key) | {"booking_token": token}, timeout=60)
    r.raise_for_status()
    return r.json()


def _vuelo_base(item: dict, tramo: Tramo, cfg: Config, run_id: str, ahora: str) -> Observacion | None:
    segs = item.get("flights") or []
    if not segs or not item.get("price"):
        return None
    primero, ultimo = segs[0], segs[-1]
    salida = primero["departure_airport"]["time"]  # "YYYY-MM-DD HH:MM"
    llegada = ultimo["arrival_airport"]["time"]
    if not salida.startswith(tramo.fecha.isoformat()):
        return None
    aerolinea = " + ".join(dict.fromkeys(s.get("airline", "") for s in segs))
    return Observacion(
        run_id=run_id,
        observado_utc=ahora,
        fuente=FUENTE,
        vendedor=aerolinea,
        tipo_vendedor="aerolinea",
        tramo=tramo.nombre,
        origen=primero["departure_airport"]["id"],
        destino=ultimo["arrival_airport"]["id"],
        fecha_vuelo=tramo.fecha.isoformat(),
        hora_salida=salida[-5:],
        hora_llegada=llegada[-5:],
        franja=cfg.franja_de(int(salida[-5:-3])),
        aerolinea=aerolinea,
        vuelo=" / ".join(s.get("flight_number", "") for s in segs),
        escalas=len(segs) - 1,
        duracion_min=int(item.get("total_duration") or sum(s.get("duration", 0) for s in segs)),
        precio=int(item["price"]),
        moneda=cfg.moneda,
        pasajeros=cfg.adultos,
    )


def parse_booking_options(data: dict, base: Observacion) -> list[Observacion]:
    """Una observación por vendedor (aerolínea u OTA) que ofrece ese vuelo."""
    out: list[Observacion] = []
    aerolineas = {a.strip().lower() for a in base.aerolinea.split("+")}
    for opt in data.get("booking_options", []):
        bloque = opt.get("together") or opt.get("departing") or {}
        vendedor = (bloque.get("book_with") or "").strip()
        precio = bloque.get("price")
        if not vendedor or not precio:
            continue
        es_aerolinea = bool(bloque.get("airline")) or vendedor.lower() in aerolineas
        out.append(
            Observacion(
                **(base.as_row() | {
                    "vendedor": vendedor,
                    "tipo_vendedor": "aerolinea" if es_aerolinea else "agencia",
                    "precio": int(precio),
                })
            )
        )
    return out


def buscar(cfg: Config, run_id: str, ahora: str) -> ResultadoFuente:
    res = ResultadoFuente(FUENTE)
    api_key = os.environ.get("SERPAPI_KEY", "").strip()
    if not api_key:
        log.info("SERPAPI_KEY no configurada: se omite la fuente de agencias.")
        return res

    for tramo in cfg.tramos:
        try:
            res.llamadas += 1
            data = _busqueda(tramo, cfg, api_key)
        except Exception as e:  # noqa: BLE001
            res.errores.append(f"serpapi {tramo.nombre}: {type(e).__name__}: {e}")
            continue

        items = (data.get("best_flights") or []) + (data.get("other_flights") or [])
        pares: list[tuple[dict, Observacion]] = []
        for it in items:
            ob = _vuelo_base(it, tramo, cfg, run_id, ahora)
            if ob and not (cfg.solo_directos and ob.escalas > 0):
                res.observaciones.append(ob)
                pares.append((it, ob))

        pares.sort(key=lambda p: p[1].precio)
        for it, ob in pares[: cfg.serpapi_top_n]:
            token = it.get("booking_token")
            if not token:
                continue
            try:
                res.llamadas += 1
                res.observaciones.extend(parse_booking_options(_booking_options(token, cfg, api_key), ob))
            except Exception as e:  # noqa: BLE001
                res.errores.append(f"serpapi booking {ob.vuelo}: {type(e).__name__}: {e}")
        log.info("serpapi %s: %d filas", tramo.nombre, len(res.observaciones))
    return res
