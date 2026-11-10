"""Alerta opcional por Telegram cuando el viaje completo marca nuevo mínimo histórico."""
from __future__ import annotations

import logging
import os

import requests

from .report import cop

log = logging.getLogger(__name__)


def mensaje(d: dict, url_dashboard: str | None) -> str:
    c = d["combo_actual"]
    lineas = [f"🔥 Nuevo mínimo MDE⇄BOG: {cop(c['total'])} ({d['pasajeros']} adulto/s)"]
    for p in c["tramos"]:
        lineas.append(f"• {p['fecha_vuelo']} {p['hora_salida']} {p['origen']}→{p['destino']} "
                      f"{p['aerolinea']} {cop(p['precio'])} ({p['vendedor']})")
    if url_dashboard:
        lineas.append(url_dashboard)
    return "\n".join(lineas)


def telegram(d: dict, url_dashboard: str | None = None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat and d.get("nuevo_minimo") and d.get("combo_actual")):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": mensaje(d, url_dashboard)}, timeout=30)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 - una alerta fallida no debe tumbar el pipeline
        log.warning("No se pudo enviar Telegram: %s", e)
        return False
