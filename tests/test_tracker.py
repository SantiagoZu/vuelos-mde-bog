"""Pruebas sin red: simulan respuestas de Google Flights y SerpApi."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from tracker import report, storage
from tracker.config import load_config
from tracker.models import Observacion
from tracker.sources import google_flights, serpapi


@pytest.fixture
def cfg():
    return load_config()


def _gf_item(price, hh, mm, airline="Avianca", code="AV", num="8501", date=(2027, 3, 20), frm="MDE", to="BOG", segs=1):
    seg = NS(
        from_airport=NS(code=frm, name=frm), to_airport=NS(code=to, name=to),
        departure=NS(date=date, time=(hh, mm)), arrival=NS(date=date, time=(hh + 1, mm)),
        duration=60, plane_type="A320", airline_code=code, flight_number=num,
    )
    return NS(price=price, airlines=[airline], flights=[seg] * segs)


def test_franjas(cfg):
    assert cfg.franja_de(5) == "madrugada"
    assert cfg.franja_de(6) == "manana"
    assert cfg.franja_de(13) == "mediodia"
    assert cfg.franja_de(17) == "tarde"
    assert cfg.franja_de(23) == "noche"


def test_google_flights_parse_filtra(cfg):
    tramo = cfg.tramos[0]
    items = [
        _gf_item(250000, 5, 30),
        _gf_item(180000, 21, 10, airline="JetSMART", code="JA", num="5201"),
        _gf_item(0, 9, 0),                       # sin precio -> fuera
        _gf_item(99000, 9, 0, segs=2),           # con escala -> fuera (solo_directos)
        _gf_item(99000, 9, 0, date=(2027, 3, 21)),  # otra fecha -> fuera
    ]
    obs = google_flights.a_observaciones(items, tramo, cfg, "R1", "2026-11-10T11:41:00Z")
    assert [(o.franja, o.precio, o.aerolinea) for o in obs] == [
        ("madrugada", 250000, "Avianca"), ("noche", 180000, "JetSMART")]
    assert obs[0].vuelo == "AV 8501" and obs[0].tramo == "ida"


def test_serpapi_booking_options(cfg):
    tramo = cfg.tramos[1]
    item = {
        "flights": [{"departure_airport": {"id": "BOG", "time": "2027-03-23 07:15"},
                     "arrival_airport": {"id": "MDE", "time": "2027-03-23 08:20"},
                     "airline": "LATAM", "flight_number": "LA 4023", "duration": 65}],
        "price": 210000, "booking_token": "tok",
    }
    base = serpapi._vuelo_base(item, tramo, cfg, "R1", "2026-11-10T11:41:00Z")
    assert base.franja == "manana" and base.precio == 210000
    data = {"booking_options": [
        {"together": {"book_with": "LATAM", "price": 210000, "airline": True}},
        {"together": {"book_with": "Despegar", "price": 199500}},
        {"together": {"book_with": "Sin precio"}},
    ]}
    out = serpapi.parse_booking_options(data, base)
    assert [(o.vendedor, o.tipo_vendedor, o.precio) for o in out] == [
        ("LATAM", "aerolinea", 210000), ("Despegar", "agencia", 199500)]


def _obs(run, ts, tramo, hora, precio, vendedor="Avianca", tipo="aerolinea", cfg=None):
    return Observacion(run, ts, "google_flights", vendedor, tipo, tramo, "MDE", "BOG",
                       "2027-03-20", hora, "23:59", cfg.franja_de(int(hora[:2])), "Avianca",
                       "AV 1", 0, 60, precio, "COP", 1)


def test_reporte_y_nuevo_minimo(cfg, tmp_path, monkeypatch):
    csv = tmp_path / "obs.csv"
    filas = [
        _obs("R1", "2026-11-10T11:41:00Z", "ida", "05:30", 300000, cfg=cfg),
        _obs("R1", "2026-11-10T11:41:00Z", "vuelta", "19:00", 280000, cfg=cfg),
        _obs("R2", "2026-11-11T11:41:00Z", "ida", "05:30", 260000, cfg=cfg),
        _obs("R2", "2026-11-11T11:41:00Z", "ida", "15:00", 240000, cfg=cfg),
        _obs("R2", "2026-11-11T11:41:00Z", "vuelta", "19:00", 250000, cfg=cfg),
        _obs("R2", "2026-11-11T11:41:00Z", "vuelta", "19:00", 230000, vendedor="Despegar", tipo="agencia", cfg=cfg),
    ]
    storage.agregar(filas, csv)
    rows = storage.leer(csv)
    assert rows[0]["precio"] == 300000  # tipos enteros al leer

    d = report.construir(rows, cfg)
    assert d["ultimo_run"] == "R2"
    assert d["combo_actual"]["total"] == 240000 + 230000
    assert d["nuevo_minimo"] is True
    ida = {r["franja"]: r["precio"] for r in d["actual"]["ida"]}
    assert ida["madrugada"] == 260000 and ida["tarde"] == 240000 and ida["noche"] is None
    assert d["historico"]["combo"] == [580000, 470000]
    assert d["agencias"][0]["vendedor"] == "Despegar"
    assert d["minimos"]["ida"]["madrugada"]["dia"] == "2026-11-11"

    md = report.markdown(d)
    assert "Nuevo mínimo histórico" in md and "$470.000" in md


def test_reporte_vacio(cfg):
    d = report.construir([], cfg)
    assert d["vacio"] and "Aún no hay datos" in report.markdown(d)


def test_hora_en_punto_y_dedup(cfg):
    """Google manda (7, None) para las 7:00; y la misma tarifa puede llegar dos veces."""
    tramo = cfg.tramos[0]
    it = _gf_item(200000, 7, 0)
    it.flights[0].departure.time = (7, None)
    it.flights[0].arrival.time = (8, None)
    obs = google_flights.a_observaciones([it, it], tramo, cfg, "R1", "2026-11-10T11:41:00Z")
    assert len(obs) == 1 and obs[0].hora_salida == "07:00" and obs[0].franja == "manana"


def test_parse_html_tolera_texto_extra(monkeypatch):
    import fast_flights.parser as P
    capturado = {}
    monkeypatch.setattr(P, "parse_payload", lambda payload: capturado.setdefault("p", payload))
    html = ("<html><script class=\"ds:1\">AF_initDataCallback({key: 'ds:1', hash: '2', "
            "data:[[1,2],[3,\"a,b\"]], sideChannel: {}, extra: [9,9]});</script></html>")
    google_flights._parse_html(html)
    assert capturado["p"] == [[1, 2], [3, "a,b"]]


# ----------------------------------------------------------------- alertas
from datetime import datetime, timedelta, timezone

from tracker import alerts


def _historia(cfg, totales):
    """Una ejecución cada 12 h; cada una con ida y vuelta que suman `total`."""
    filas, t0 = [], datetime(2026, 11, 10, 11, 41, tzinfo=timezone.utc)
    for i, total in enumerate(totales):
        t = t0 + timedelta(hours=12 * i)
        run, ts = t.strftime("%Y%m%dT%H%MZ"), t.strftime("%Y-%m-%dT%H:%M:00Z")
        filas.append(_obs(run, ts, "ida", "06:30", total // 2, cfg=cfg).as_row())
        filas.append(_obs(run, ts, "vuelta", "19:00", total - total // 2, cfg=cfg).as_row())
    return filas


def _reglas(cfg, **kw):
    base = {"nuevo_minimo_historico": False, "caida_pct_vs_anterior": None, "caida_pct_vs_promedio_7d": None,
            "precio_objetivo": None, "precio_objetivo_tramo": None, "caida_pct_franja": None}
    cfg.alertas = {"reglas": base | kw, "canales": {"github_issue": True, "telegram": True}, "enfriamiento_horas": 24}
    return cfg


def _evaluar(cfg, totales):
    filas = _historia(cfg, totales)
    return alerts.evaluar(filas, report.construir(filas, cfg), cfg)


def test_alerta_caida_vs_anterior(cfg):
    _reglas(cfg, caida_pct_vs_anterior=10)
    assert _evaluar(cfg, [320000, 300000]) == []                      # -6 %
    [a] = _evaluar(cfg, [320000, 280000])                               # -12.5 %
    assert a.regla == "caida_pct_vs_anterior" and "$280.000" in a.texto


def test_alerta_promedio_7d_requiere_historia(cfg):
    _reglas(cfg, caida_pct_vs_promedio_7d=12)
    assert _evaluar(cfg, [320000, 320000, 270000]) == []               # pocas ejecuciones
    [a] = _evaluar(cfg, [320000, 310000, 330000, 320000, 270000])      # -16 % vs 320k
    assert a.regla == "caida_pct_vs_promedio_7d"


def test_alerta_objetivo_minimo_y_franja(cfg):
    _reglas(cfg, nuevo_minimo_historico=True, precio_objetivo=300000, caida_pct_franja=20)
    reglas = {a.regla for a in _evaluar(cfg, [400000, 290000])}
    assert reglas == {"nuevo_minimo_historico", "precio_objetivo",
                      "caida_pct_franja:ida:manana", "caida_pct_franja:vuelta:noche"}


def test_anti_spam():
    ahora = datetime(2026, 11, 20, tzinfo=timezone.utc)
    a = alerts.Alerta("caida_pct_vs_anterior", 280000, "x")
    o = alerts.Alerta("precio_objetivo", 290000, "y")
    estado = {"caida_pct_vs_anterior": {"precio": 280000, "cuando": (ahora - timedelta(hours=3)).isoformat()},
              "precio_objetivo": {"precio": 290000, "cuando": (ahora - timedelta(days=5)).isoformat()}}
    assert alerts.filtrar_nuevas([a, o], estado, ahora, 24) == []        # mismo precio, en enfriamiento
    assert alerts.filtrar_nuevas([a], estado, ahora + timedelta(hours=22), 24) == [a]   # pasó el enfriamiento
    mas_bajo = alerts.Alerta("precio_objetivo", 285000, "z")
    assert alerts.filtrar_nuevas([mas_bajo], estado, ahora, 24) == [mas_bajo]


def test_procesar_envia_issue_y_telegram(cfg, tmp_path, monkeypatch):
    _reglas(cfg, caida_pct_vs_anterior=10)
    monkeypatch.setattr(alerts, "ESTADO_PATH", tmp_path / "estado.json")
    for k, v in {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "u/r", "GITHUB_REPOSITORY_OWNER": "u",
                 "TELEGRAM_BOT_TOKEN": "b", "TELEGRAM_CHAT_ID": "1", "DASHBOARD_URL": "https://u.github.io/r/"}.items():
        monkeypatch.setenv(k, v)
    llamadas = []

    class R:
        def __init__(self, data=None): self.status_code, self._d = 200, data
        def raise_for_status(self): pass
        def json(self): return self._d

    def fake(method):
        def f(url, **kw):
            llamadas.append((method, url, kw.get("json")))
            return R([{"number": 3}] if method == "GET" else {"html_url": "https://github.com/u/r/issues/4"})
        return f
    monkeypatch.setattr(alerts.requests, "get", fake("GET"))
    monkeypatch.setattr(alerts.requests, "post", fake("POST"))
    monkeypatch.setattr(alerts.requests, "patch", fake("PATCH"))

    filas = _historia(cfg, [320000, 280000])
    d = report.construir(filas, cfg)
    assert len(alerts.procesar(filas, d, cfg)) == 1
    metodos = [(m, u.rsplit("/", 2)[-2:]) for m, u, _ in llamadas]
    assert ("POST", ["r", "issues"]) in metodos and ("PATCH", ["issues", "3"]) in metodos
    issue = next(j for m, u, j in llamadas if m == "POST" and u.endswith("/issues"))
    assert "cc @u" in issue["body"] and "$280.000" in issue["title"]
    assert any("telegram" in u for _, u, _ in llamadas)
    # segunda vez con el mismo precio: no reenvía
    llamadas.clear()
    assert alerts.procesar(filas, d, cfg) == [] and llamadas == []


def test_alerta_objetivo_por_tramo(cfg):
    _reglas(cfg, precio_objetivo_tramo=100000)
    assert _evaluar(cfg, [320000, 210000]) == []                        # 105k por trayecto
    alertas = _evaluar(cfg, [320000, 190000])                            # 95k por trayecto
    assert {a.regla for a in alertas} == {"precio_objetivo_tramo:ida", "precio_objetivo_tramo:vuelta"}
    # solo vuelve a avisar si baja más, no por enfriamiento
    ahora = datetime(2026, 11, 27, tzinfo=timezone.utc)
    estado = {a.regla: {"precio": a.precio, "cuando": (ahora - timedelta(days=3)).isoformat()} for a in alertas}
    assert alerts.filtrar_nuevas(alertas, estado, ahora, 24) == []
