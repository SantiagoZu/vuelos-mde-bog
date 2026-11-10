"""Fuente principal: Google Flights a través de la librería `faster-flights` (módulo fast_flights).

Google Flights agrega las tarifas publicadas por las propias aerolíneas, así que
de aquí salen Avianca, LATAM, JetSMART, Wingo, Clic... sin API key y sin pagar.
"""
from __future__ import annotations

import json
import logging
import random
import time
from itertools import product

from ..config import ROOT, Config, Tramo
from ..models import Observacion
from . import ResultadoFuente

log = logging.getLogger(__name__)

FUENTE = "google_flights"
REINTENTOS = 3


DEBUG_DIR = ROOT / "debug"


def _parse_html(html: str):
    """Parser tolerante: la librería corta el JSON con rsplit y falla con algunas
    variantes de la página; aquí se decodifica solo el primer valor JSON válido."""
    from fast_flights.parser import parse_payload
    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise ValueError("La página no trae el bloque de datos ds:1 (¿bloqueo o cambio de estructura?)")
    texto = script.text()
    i = texto.find("data:")
    if i < 0:
        raise ValueError("El bloque ds:1 no contiene 'data:'")
    payload, _ = json.JSONDecoder().raw_decode(texto[i + 5:].lstrip())
    return parse_payload(payload)


def _guardar_debug(nombre: str, html: str) -> None:
    DEBUG_DIR.mkdir(exist_ok=True)
    (DEBUG_DIR / f"{nombre}.html").write_text(html, encoding="utf-8")


def _consultar(origen: str, destino: str, tramo: Tramo, cfg: Config):
    """Combina dos vías de Google Flights: la página HTML y el endpoint RPC de
    'shopping' (ordenado por precio). Si una falla, la otra suele responder."""
    from fast_flights import (FlightQuery, Passengers, ShoppingOptions, create_query,
                              fetch_flights_html, get_flights)

    query = create_query(
        flights=[FlightQuery(date=tramo.fecha.isoformat(), from_airport=origen, to_airport=destino)],
        trip="one-way",
        seat="economy",
        passengers=Passengers(adults=cfg.adultos),
        language=cfg.idioma,
        currency=cfg.moneda,
        max_stops=0 if cfg.solo_directos else None,
    )
    vuelos, errores = [], []

    html = None
    try:
        html = fetch_flights_html(query)
        vuelos += list(_parse_html(html))
    except Exception as e:  # noqa: BLE001
        errores.append(f"html: {type(e).__name__}: {e}")
        log.debug("Detalle html", exc_info=True)
        if html:
            _guardar_debug(f"{tramo.nombre}_{origen}_{destino}", html)

    try:
        vuelos += list(get_flights(query, shopping=ShoppingOptions(ranking_mode="cheapest", result_sort="price")))
    except Exception as e:  # noqa: BLE001
        errores.append(f"rpc: {type(e).__name__}: {e}")
        log.debug("Detalle rpc", exc_info=True)

    if errores:
        log.warning("%s %s->%s: %s", tramo.nombre, origen, destino, " | ".join(errores))
    if not vuelos and errores:
        raise RuntimeError(" | ".join(errores))
    return vuelos


def _hhmm(t) -> str:
    # Google omite los ceros: un vuelo a las 7:00 llega como (7, None)
    h = t[0] or 0
    m = (t[1] or 0) if len(t) > 1 else 0
    return f"{h:02d}:{m:02d}"


def a_observaciones(resultados, tramo: Tramo, cfg: Config, run_id: str, ahora: str) -> list[Observacion]:
    """Convierte la lista de `Flights` de fast_flights en filas del histórico."""
    obs: list[Observacion] = []
    vistos: set[tuple] = set()
    for it in resultados:
        if not it.flights or not it.price or it.price <= 0:
            continue
        primero, ultimo = it.flights[0], it.flights[-1]
        fecha = "%04d-%02d-%02d" % tuple(x or 0 for x in primero.departure.date)
        if fecha != tramo.fecha.isoformat():
            continue
        escalas = len(it.flights) - 1
        if cfg.solo_directos and escalas > 0:
            continue
        aerolinea = " + ".join(dict.fromkeys(it.airlines)) or primero.airline_code
        vuelo = " / ".join(
            f"{s.airline_code} {s.flight_number}".strip() for s in it.flights if s.flight_number or s.airline_code
        )
        clave = (vuelo, _hhmm(primero.departure.time), int(it.price))
        if clave in vistos:  # la misma tarifa puede venir por HTML y por RPC
            continue
        vistos.add(clave)
        obs.append(
            Observacion(
                run_id=run_id,
                observado_utc=ahora,
                fuente=FUENTE,
                vendedor=aerolinea,
                tipo_vendedor="aerolinea",
                tramo=tramo.nombre,
                origen=primero.from_airport.code,
                destino=ultimo.to_airport.code,
                fecha_vuelo=fecha,
                hora_salida=_hhmm(primero.departure.time),
                hora_llegada=_hhmm(ultimo.arrival.time),
                franja=cfg.franja_de(primero.departure.time[0] or 0),
                aerolinea=aerolinea,
                vuelo=vuelo,
                escalas=escalas,
                duracion_min=sum(s.duration for s in it.flights),
                precio=int(it.price),
                moneda=cfg.moneda,
                pasajeros=cfg.adultos,
            )
        )
    return obs


def buscar(cfg: Config, run_id: str, ahora: str) -> ResultadoFuente:
    res = ResultadoFuente(FUENTE)
    for tramo in cfg.tramos:
        for origen, destino in product(tramo.origenes, tramo.destinos):
            if origen == destino:
                continue
            ultimo_error = None
            for intento in range(1, REINTENTOS + 1):
                res.llamadas += 1
                try:
                    vuelos = _consultar(origen, destino, tramo, cfg)
                    filas = a_observaciones(vuelos, tramo, cfg, run_id, ahora)
                    log.info("google_flights %s %s->%s: %d vuelos", tramo.nombre, origen, destino, len(filas))
                    res.observaciones.extend(filas)
                    ultimo_error = None
                    break
                except Exception as e:  # noqa: BLE001 - scraping: cualquier fallo se reintenta
                    ultimo_error = f"{tramo.nombre} {origen}->{destino}: {type(e).__name__}: {e}"
                    log.warning("Intento %d falló (%s)", intento, ultimo_error)
                    time.sleep(5 * intento + random.uniform(0, 3))
            if ultimo_error:
                res.errores.append(ultimo_error)
            time.sleep(random.uniform(2, 5))  # no martillar a Google
    return res
