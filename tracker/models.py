from __future__ import annotations

from dataclasses import asdict, dataclass, fields


@dataclass
class Observacion:
    """Una fila del histórico: un precio visto para un vuelo concreto en una ejecución."""

    run_id: str              # p.ej. 20261110T1117Z
    observado_utc: str       # ISO-8601
    fuente: str              # google_flights | serpapi
    vendedor: str            # quién vende: aerolínea u OTA (Despegar, Expedia...)
    tipo_vendedor: str       # aerolinea | agencia
    tramo: str               # ida | vuelta
    origen: str
    destino: str
    fecha_vuelo: str         # YYYY-MM-DD
    hora_salida: str         # HH:MM local
    hora_llegada: str
    franja: str
    aerolinea: str
    vuelo: str               # número de vuelo, p.ej. AV 8501
    escalas: int
    duracion_min: int
    precio: int
    moneda: str
    pasajeros: int

    @classmethod
    def columnas(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_row(self) -> dict:
        return asdict(self)
