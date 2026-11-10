"""Agrega el histórico y genera: docs/data.json (dashboard) y reports/ultimo.md (resumen)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import ROOT, Config

TZ = ZoneInfo("America/Bogota")
DATA_JSON = ROOT / "docs" / "data.json"
REPORTE_MD = ROOT / "reports" / "ultimo.md"


def dia_local(iso_utc: str) -> str:
    return datetime.fromisoformat(iso_utc.replace("Z", "+00:00")).astimezone(TZ).date().isoformat()


def cop(n: int | None) -> str:
    return "—" if n is None else "$" + f"{n:,.0f}".replace(",", ".")


def _min_por(filas, *keys):
    """Fila más barata por combinación de claves."""
    mejor: dict[tuple, dict] = {}
    for r in filas:
        k = tuple(r[c] for c in keys)
        if k not in mejor or r["precio"] < mejor[k]["precio"]:
            mejor[k] = r
    return mejor


def _ficha(r: dict) -> dict:
    campos = ("precio", "aerolinea", "vendedor", "tipo_vendedor", "hora_salida", "hora_llegada",
              "vuelo", "origen", "destino", "fuente", "franja", "fecha_vuelo")
    return {c: r[c] for c in campos}


def construir(filas: list[dict], cfg: Config) -> dict:
    tramos = [t.nombre for t in cfg.tramos]
    franjas = [f.nombre for f in cfg.franjas]
    if not filas:
        return {"generado": datetime.now(TZ).isoformat(timespec="minutes"), "vacio": True,
                "tramos": tramos, "franjas": franjas}

    for r in filas:
        r["dia"] = dia_local(r["observado_utc"])

    runs = sorted({r["run_id"] for r in filas})
    ultimo_run = runs[-1]
    ult = [r for r in filas if r["run_id"] == ultimo_run]

    # --- Foto actual: más barato por tramo × franja (cualquier vendedor) ---
    por_franja = _min_por(ult, "tramo", "franja")
    actual = {
        t: [
            ({"franja": f} | _ficha(por_franja[(t, f)])) if (t, f) in por_franja else {"franja": f, "precio": None}
            for f in franjas
        ]
        for t in tramos
    }
    por_aerolinea = {
        t: sorted((_ficha(r) for (tt, _), r in _min_por(ult, "tramo", "aerolinea").items() if tt == t),
                  key=lambda x: x["precio"])
        for t in tramos
    }

    # Agencias: última ejecución que trajo datos de agencias (SerpApi corre 1 vez/día)
    con_agencias = [r for r in filas if r["tipo_vendedor"] == "agencia"]
    agencias = []
    if con_agencias:
        run_ag = max(r["run_id"] for r in con_agencias)
        agencias = sorted(
            ({"tramo": r["tramo"]} | _ficha(r) for r in con_agencias if r["run_id"] == run_ag),
            key=lambda x: (tramos.index(x["tramo"]) if x["tramo"] in tramos else 99, x["precio"]),
        )

    # --- Viaje completo (ida más barata + vuelta más barata) por ejecución ---
    min_run_tramo = _min_por(filas, "run_id", "tramo")

    def combo(run):
        partes = [min_run_tramo.get((run, t)) for t in tramos]
        if any(p is None for p in partes):
            return None
        return sum(p["precio"] for p in partes), partes

    combos = {run: c for run in runs if (c := combo(run))}
    combo_actual = combos.get(ultimo_run)
    previos = [c[0] for run, c in combos.items() if run != ultimo_run]
    nuevo_minimo = bool(combo_actual and previos and combo_actual[0] < min(previos))

    # --- Serie diaria (mínimo del día) por tramo × franja y del viaje completo ---
    dias = sorted({r["dia"] for r in filas})
    min_dia = _min_por(filas, "dia", "tramo", "franja")
    serie = {t: {f: [min_dia[(d, t, f)]["precio"] if (d, t, f) in min_dia else None for d in dias]
                 for f in franjas} for t in tramos}
    min_dia_tramo = _min_por(filas, "dia", "tramo")
    serie_combo = []
    for d in dias:
        p = [min_dia_tramo.get((d, t)) for t in tramos]
        serie_combo.append(None if any(x is None for x in p) else sum(x["precio"] for x in p))

    # --- Mínimos históricos por tramo × franja ---
    hist = _min_por(filas, "tramo", "franja")
    minimos = {t: {f: (_ficha(hist[(t, f)]) | {"dia": hist[(t, f)]["dia"]}) if (t, f) in hist else None
                   for f in franjas} for t in tramos}
    mejor_combo_hist = min(combos.values(), key=lambda c: c[0]) if combos else None

    return {
        "generado": datetime.now(TZ).isoformat(timespec="minutes"),
        "moneda": cfg.moneda,
        "pasajeros": cfg.adultos,
        "tramos": tramos,
        "franjas": franjas,
        "fechas": {t.nombre: t.fecha.isoformat() for t in cfg.tramos},
        "rutas": {t.nombre: f"{'/'.join(t.origenes)} → {'/'.join(t.destinos)}" for t in cfg.tramos},
        "ultimo_run": ultimo_run,
        "ultimo_observado": ult[0]["observado_utc"],
        "ejecuciones": len(runs),
        "actual": actual,
        "por_aerolinea": por_aerolinea,
        "agencias": agencias,
        "combo_actual": {"total": combo_actual[0], "tramos": [_ficha(p) for p in combo_actual[1]]} if combo_actual else None,
        "combo_minimo_historico": {"total": mejor_combo_hist[0], "tramos": [_ficha(p) | {"dia": p["dia"]} for p in mejor_combo_hist[1]]} if mejor_combo_hist else None,
        "nuevo_minimo": nuevo_minimo,
        "combos": [{"run": run, "total": c[0]} for run, c in sorted(combos.items())],
        "historico": {"dias": dias, "series": serie, "combo": serie_combo},
        "minimos": minimos,
    }


def markdown(d: dict) -> str:
    if d.get("vacio"):
        return "# ✈️ Vuelos MDE ⇄ BOG\n\nAún no hay datos.\n"
    L = [f"# ✈️ Vuelos Medellín ⇄ Bogotá — {d['generado'][:16].replace('T', ' ')} (hora Colombia)", ""]
    c = d["combo_actual"]
    if c:
        flag = " 🔥 **¡Nuevo mínimo histórico!**" if d["nuevo_minimo"] else ""
        L += [f"**Viaje completo más barato ahora: {cop(c['total'])}** ({d['pasajeros']} adulto(s)){flag}", ""]
        for p in c["tramos"]:
            L.append(f"- {p['fecha_vuelo']} {p['origen']}→{p['destino']} {p['hora_salida']} · {p['aerolinea']} "
                     f"{p['vuelo']} · {cop(p['precio'])} vía {p['vendedor']}")
        L.append("")
    h = d["combo_minimo_historico"]
    if h:
        L += [f"Mínimo histórico del viaje completo: **{cop(h['total'])}**", ""]
    for t in d["tramos"]:
        L += [f"## {t.capitalize()} · {d['fechas'][t]} · {d['rutas'][t]}", "",
              "| Franja | Ahora | Salida | Aerolínea | Vendedor | Mín. histórico (día) |",
              "|---|---:|---|---|---|---|"]
        for row in d["actual"][t]:
            m = d["minimos"][t][row["franja"]]
            mh = f"{cop(m['precio'])} ({m['dia']})" if m else "—"
            if row["precio"] is None:
                L.append(f"| {row['franja']} | — | | | | {mh} |")
            else:
                L.append(f"| {row['franja']} | {cop(row['precio'])} | {row['hora_salida']} {row['origen']}→{row['destino']} "
                         f"| {row['aerolinea']} | {row['vendedor']} | {mh} |")
        L.append("")
    if d["agencias"]:
        L += ["## Agencias / OTAs (último chequeo)", "", "| Tramo | Vendedor | Vuelo | Salida | Precio |", "|---|---|---|---|---:|"]
        for a in d["agencias"]:
            L.append(f"| {a['tramo']} | {a['vendedor']} | {a['aerolinea']} {a['vuelo']} | {a['hora_salida']} | {cop(a['precio'])} |")
        L.append("")
    return "\n".join(L)


def escribir(d: dict) -> str:
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    DATA_JSON.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    md = markdown(d)
    REPORTE_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORTE_MD.write_text(md, encoding="utf-8")
    return md
