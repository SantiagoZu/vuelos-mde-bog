from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Observacion


@dataclass
class ResultadoFuente:
    fuente: str
    observaciones: list[Observacion] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    llamadas: int = 0
