"""Punto de entrada.

    python -m tracker.main run       # consulta fuentes, guarda histórico y regenera reporte
    python -m tracker.main report    # solo regenera reporte/dashboard desde el CSV
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone

from . import alerts, report, storage
from .config import load_config
from .sources import google_flights, serpapi

log = logging.getLogger("tracker")


def _serpapi_ya_corrio_hoy(filas: list[dict], maximo: int) -> bool:
    hoy = datetime.now(report.TZ).date().isoformat()
    runs_hoy = {r["run_id"] for r in filas if r["fuente"] == "serpapi" and report.dia_local(r["observado_utc"]) == hoy}
    return len(runs_hoy) >= maximo


def _summary(texto: str) -> None:
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


def run(force_serpapi: bool = False) -> int:
    cfg = load_config()
    hoy = datetime.now(report.TZ).date()
    if hoy > cfg.hasta:
        log.info("Ya pasó %s: no se rastrea más.", cfg.hasta)
        return 0

    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    run_id = ahora.strftime("%Y%m%dT%H%MZ")
    ahora_iso = ahora.isoformat().replace("+00:00", "Z")
    historico = storage.leer()

    resultados = []
    if cfg.google_flights:
        resultados.append(google_flights.buscar(cfg, run_id, ahora_iso))
    if cfg.serpapi:
        if force_serpapi or not _serpapi_ya_corrio_hoy(historico, cfg.serpapi_max_por_dia):
            resultados.append(serpapi.buscar(cfg, run_id, ahora_iso))
        else:
            log.info("SerpApi ya corrió hoy (cuida la cuota gratis): se omite.")

    nuevas = [o for r in resultados for o in r.observaciones]
    storage.agregar(nuevas)

    resumen = ["### Ejecución " + run_id, "", "| Fuente | Llamadas | Filas | Errores |", "|---|---:|---:|---:|"]
    for r in resultados:
        resumen.append(f"| {r.fuente} | {r.llamadas} | {len(r.observaciones)} | {len(r.errores)} |")
        for e in r.errores:
            log.warning("%s", e)
    _summary("\n".join(resumen) + "\n")

    filas = storage.leer()
    d = report.construir(filas, cfg)
    md = report.escribir(d)
    _summary(md)
    print(md)

    if nuevas:
        enviadas = alerts.procesar(filas, d, cfg)
        if enviadas:
            _summary("### 🔔 Alertas enviadas\n\n" + "\n".join(f"- {a.texto}" for a in enviadas) + "\n")

    if not nuevas:
        log.error("Ninguna fuente devolvió precios en esta ejecución.")
        return 1
    return 0


def solo_reporte() -> int:
    d = report.construir(storage.leer(), load_config())
    print(report.escribir(d))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Tracker de vuelos MDE ⇄ BOG")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--force-serpapi", action="store_true", help="Ignora el límite diario de SerpApi")
    sub.add_parser("report")
    a = p.parse_args(argv)
    return run(a.force_serpapi) if a.cmd == "run" else solo_reporte()


if __name__ == "__main__":
    sys.exit(main())
