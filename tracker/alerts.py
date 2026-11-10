"""Detección de bajadas de precio significativas y envío de avisos.

Reglas (configurables en config.yaml > alertas.reglas):
  - nuevo_minimo_historico   el viaje completo marca el mínimo de todo el histórico
  - caida_pct_vs_anterior    el viaje completo baja X % frente a la consulta anterior
  - caida_pct_vs_promedio_7d el viaje completo queda X % bajo el promedio de 7 días
  - precio_objetivo          el viaje completo queda en o por debajo de un precio
  - caida_pct_franja         una franja concreta baja X % frente a la consulta anterior

El estado de los avisos ya enviados vive en data/alertas_estado.json (versionado),
para no repetir el mismo aviso en cada ejecución.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from .config import ROOT, Config
from .report import cop

log = logging.getLogger(__name__)

ESTADO_PATH = ROOT / "data" / "alertas_estado.json"
LABEL = "alerta-precio"
MIN_RUNS_PROMEDIO = 4  # sin suficiente historia, el promedio no significa nada


@dataclass
class Alerta:
    regla: str
    precio: int        # precio que dispara (para el anti-spam)
    texto: str


def _run_a_dt(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%dT%H%MZ").replace(tzinfo=timezone.utc)


def _pct(antes: float, ahora: float) -> float:
    return (antes - ahora) / antes * 100 if antes else 0.0


def evaluar(filas: list[dict], d: dict, cfg: Config) -> list[Alerta]:
    reglas = (cfg.alertas.get("reglas") or {})
    combos = d.get("combos") or []
    actual = d.get("combo_actual")
    if not actual or not combos:
        return []
    total = actual["total"]
    run = d["ultimo_run"]
    previos = [c for c in combos if c["run"] < run]
    out: list[Alerta] = []

    if reglas.get("nuevo_minimo_historico") and previos and d.get("nuevo_minimo"):
        antes = min(c["total"] for c in previos)
        out.append(Alerta("nuevo_minimo_historico", total,
                          f"Nuevo mínimo histórico del viaje completo: {cop(total)} (antes {cop(antes)})"))

    umbral = reglas.get("caida_pct_vs_anterior")
    if umbral is not None and previos:
        antes = previos[-1]["total"]
        p = _pct(antes, total)
        if p >= float(umbral):
            out.append(Alerta("caida_pct_vs_anterior", total,
                              f"Bajó {p:.0f} % frente a la consulta anterior: {cop(antes)} → {cop(total)}"))

    umbral = reglas.get("caida_pct_vs_promedio_7d")
    if umbral is not None:
        desde = _run_a_dt(run) - timedelta(days=7)
        ventana = [c["total"] for c in previos if _run_a_dt(c["run"]) >= desde]
        if len(ventana) >= MIN_RUNS_PROMEDIO:
            prom = sum(ventana) / len(ventana)
            p = _pct(prom, total)
            if p >= float(umbral):
                out.append(Alerta("caida_pct_vs_promedio_7d", total,
                                  f"Está {p:.0f} % por debajo del promedio de 7 días ({cop(round(prom))}): {cop(total)}"))

    objetivo = reglas.get("precio_objetivo")
    if objetivo is not None and total <= int(objetivo):
        out.append(Alerta("precio_objetivo", total,
                          f"Llegó al precio objetivo: {cop(total)} ≤ {cop(int(objetivo))}"))

    umbral = reglas.get("caida_pct_franja")
    if umbral is not None and previos:
        run_prev = previos[-1]["run"]
        def mins(rid):
            m: dict[tuple, dict] = {}
            for r in filas:
                if r["run_id"] == rid:
                    k = (r["tramo"], r["franja"])
                    if k not in m or r["precio"] < m[k]["precio"]:
                        m[k] = r
            return m
        ahora_m, antes_m = mins(run), mins(run_prev)
        for (tramo, franja), r in sorted(ahora_m.items()):
            if (tramo, franja) not in antes_m:
                continue
            antes = antes_m[(tramo, franja)]["precio"]
            p = _pct(antes, r["precio"])
            if p >= float(umbral):
                out.append(Alerta(f"caida_pct_franja:{tramo}:{franja}", r["precio"],
                                  f"{tramo.capitalize()} en franja {franja} bajó {p:.0f} %: {cop(antes)} → "
                                  f"{cop(r['precio'])} ({r['aerolinea']} {r['hora_salida']}, vía {r['vendedor']})"))
    return out


# ------------------------------------------------------------------ anti-spam

def leer_estado() -> dict:
    try:
        return json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def filtrar_nuevas(alertas: list[Alerta], estado: dict, ahora: datetime, enfriamiento_h: float) -> list[Alerta]:
    """Deja pasar una alerta si nunca se envió, si el precio es más bajo que en el
    último aviso de esa regla, o si ya pasó el enfriamiento (excepto precio_objetivo,
    que solo vuelve a avisar si el precio baja todavía más)."""
    nuevas = []
    for a in alertas:
        prev = estado.get(a.regla)
        if prev is None or a.precio < prev["precio"]:
            nuevas.append(a)
            continue
        if a.regla == "precio_objetivo":
            continue
        if ahora - datetime.fromisoformat(prev["cuando"]) >= timedelta(hours=enfriamiento_h):
            nuevas.append(a)
    return nuevas


def guardar_estado(estado: dict, enviadas: list[Alerta], ahora: datetime) -> None:
    for a in enviadas:
        estado[a.regla] = {"precio": a.precio, "cuando": ahora.isoformat(timespec="minutes")}
    ESTADO_PATH.parent.mkdir(parents=True, exist_ok=True)
    ESTADO_PATH.write_text(json.dumps(estado, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ mensajes

def _lineas_viaje(d: dict) -> list[str]:
    return [f"• {p['fecha_vuelo']} {p['hora_salida']} {p['origen']}→{p['destino']} · {p['aerolinea']} "
            f"{p['vuelo']} · {cop(p['precio'])} (vía {p['vendedor']})" for p in d["combo_actual"]["tramos"]]


def titulo(d: dict) -> str:
    return f"🔻 Bajó el precio MDE⇄BOG: {cop(d['combo_actual']['total'])} ida+vuelta"


def cuerpo(alertas: list[Alerta], d: dict, url: str | None, markdown: bool) -> str:
    L = [titulo(d), ""] if not markdown else []
    L += [("- " if markdown else "• ") + a.texto for a in alertas]
    L += ["", "**Viaje más barato ahora:**" if markdown else "Viaje más barato ahora:"]
    L += [x.replace("• ", "- ") if markdown else x for x in _lineas_viaje(d)]
    if url:
        L += ["", f"Dashboard: {url}"]
    return "\n".join(L)


# ------------------------------------------------------------------ canales

def enviar_telegram(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": texto, "disable_web_page_preview": True}, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 - un aviso fallido no debe tumbar el pipeline
        log.warning("Telegram falló: %s", e)
        return False


def enviar_issue(titulo_: str, cuerpo_: str) -> bool:
    """Crea un issue y menciona al dueño del repo para que GitHub le notifique
    (correo + app). Cierra los avisos anteriores para no acumular issues."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "").strip()
    if not (token and repo):
        return False
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        viejos = requests.get(f"{api}/issues", headers=h, params={"labels": LABEL, "state": "open"}, timeout=30)
        viejos.raise_for_status()
        body = cuerpo_ + (f"\n\ncc @{owner}" if owner else "")
        r = requests.post(f"{api}/issues", headers=h, json={"title": titulo_, "body": body, "labels": [LABEL]}, timeout=30)
        if r.status_code == 422:  # por si la etiqueta no se puede crear
            r = requests.post(f"{api}/issues", headers=h, json={"title": titulo_, "body": body}, timeout=30)
        r.raise_for_status()
        nuevo = r.json().get("html_url", "")
        for it in viejos.json():
            requests.patch(f"{api}/issues/{it['number']}", headers=h, timeout=30,
                           json={"state": "closed", "state_reason": "completed"})
        log.info("Issue de alerta creado: %s", nuevo)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("No se pudo crear el issue de alerta: %s", e)
        return False


def procesar(filas: list[dict], d: dict, cfg: Config, ahora: datetime | None = None) -> list[Alerta]:
    ahora = ahora or datetime.now(timezone.utc)
    disparadas = evaluar(filas, d, cfg)
    if not disparadas:
        log.info("Sin bajadas significativas.")
        return []
    estado = leer_estado()
    nuevas = filtrar_nuevas(disparadas, estado, ahora, float(cfg.alertas.get("enfriamiento_horas", 24)))
    if not nuevas:
        log.info("Hay bajadas, pero ya se avisaron (anti-spam).")
        return []

    canales = cfg.alertas.get("canales") or {}
    url = os.environ.get("DASHBOARD_URL")
    enviado = False
    if canales.get("github_issue", True):
        enviado |= enviar_issue(titulo(d), cuerpo(nuevas, d, url, markdown=True))
    if canales.get("telegram", True):
        enviado |= enviar_telegram(cuerpo(nuevas, d, url, markdown=False))
    for a in nuevas:
        log.info("ALERTA %s: %s", a.regla, a.texto)
    if enviado:
        guardar_estado(estado, nuevas, ahora)
    return nuevas
