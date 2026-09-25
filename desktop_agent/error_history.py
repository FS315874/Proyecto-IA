"""Historial local y acotado de incidentes, independiente de interfaces/proveedores."""
import logging
import os
import re
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from desktop_agent import __version__
from desktop_agent.diagnostics import DEFINITIONS, SOURCES, STAGES, TOOLS, safe_duration

MAX_INCIDENTS = 500


class HistoryUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("No se pudo acceder al historial de errores; no se borró ni reemplazó.")


def default_error_history_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    folder = Path(base) / "DesktopAgent" if base and Path(base).is_absolute() else Path.home() / ".desktop_agent"
    return folder / "error_history.sqlite3"


@dataclass(frozen=True)
class Incident:
    id: int
    occurred_at: str
    version: str
    session: str
    code: str
    source: str
    stage: str | None
    tool: str | None
    duration_ms: float | None
    reviewed: bool

    def report(self) -> dict:
        return {**asdict(self), **asdict(DEFINITIONS[self.code])}


def _validate_fields(fields: dict) -> None:
    if not isinstance(fields, dict) or set(fields) != {"code", "source", "stage", "tool", "duration_ms"}:
        raise ValueError("Campos de diagnóstico inválidos.")
    for key, allowed in (("code", DEFINITIONS), ("source", SOURCES),
                         ("stage", STAGES | {None}), ("tool", TOOLS | {None})):
        value = fields[key]
        if value is not None and not isinstance(value, str):
            raise ValueError("Valor de diagnóstico inválido.")
        if value not in allowed:
            raise ValueError("Valor de diagnóstico no permitido.")
    value = fields["duration_ms"]
    if value is not None and safe_duration(value) is None:
        raise ValueError("Duración de diagnóstico inválida.")


class ErrorHistory:
    def __init__(self, path: Path | None = None, *, limit: int = MAX_INCIDENTS):
        if type(limit) is not int or not 1 <= limit <= MAX_INCIDENTS:
            raise ValueError("Límite de historial inválido.")
        self.path = path or default_error_history_path()
        self.limit = limit

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=0.2)
            else:
                connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
            connection.execute("PRAGMA trusted_schema=OFF")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and write:
                if connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                    raise HistoryUnavailable()
                connection.execute("""CREATE TABLE incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL,
                    version TEXT NOT NULL, session TEXT NOT NULL, code TEXT NOT NULL,
                    source TEXT NOT NULL, stage TEXT, tool TEXT, duration_ms REAL,
                    reviewed INTEGER NOT NULL DEFAULT 0)""")
                connection.execute("PRAGMA user_version=1")
            elif version != 1:
                raise HistoryUnavailable()
            yield connection
            if write:
                connection.commit()
        except (OSError, sqlite3.Error) as error:
            raise HistoryUnavailable() from error
        finally:
            if connection is not None:
                connection.close()

    def record(self, fields: dict, session: str) -> None:
        _validate_fields(fields)
        if not isinstance(session, str) or re.fullmatch(r"[0-9a-f]{32}", session) is None:
            raise ValueError("Sesión de diagnóstico inválida.")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._connection(write=True) as connection:
            connection.execute("""INSERT INTO incidents
                (occurred_at, version, session, code, source, stage, tool, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (timestamp, __version__, session,
                fields["code"], fields["source"], fields["stage"], fields["tool"], fields["duration_ms"]))
            # La retención descarta sólo los más antiguos, nunca otros archivos.
            connection.execute("DELETE FROM incidents WHERE id NOT IN (SELECT id FROM incidents ORDER BY id DESC LIMIT ?)", (self.limit,))

    def list(self, *, include_reviewed=False) -> tuple[Incident, ...]:
        if type(include_reviewed) is not bool:
            raise ValueError("El filtro de revisión debe ser booleano.")
        if not self.path.exists():
            return ()
        with self._connection() as connection:
            rows = connection.execute("""SELECT id, occurred_at, version, session,
                code, source, stage, tool, duration_ms, reviewed FROM incidents
                WHERE (? OR reviewed=0) ORDER BY id DESC LIMIT ?""", (int(include_reviewed), self.limit)).fetchall()
        try:
            incidents = []
            for row in rows:
                incident = Incident(*row[:-1], bool(row[-1]))
                _validate_fields({key: getattr(incident, key) for key in ("code", "source", "stage", "tool", "duration_ms")})
                if (type(incident.id) is not int or incident.id <= 0 or row[-1] not in (0, 1)
                    or re.fullmatch(r"[0-9a-f]{32}", incident.session) is None
                    or re.fullmatch(r"\d+\.\d+\.\d+", incident.version) is None
                    or re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", incident.occurred_at) is None):
                    raise ValueError("Fila de historial inválida.")
                datetime.fromisoformat(incident.occurred_at)
                incidents.append(incident)
            return tuple(incidents)
        except (TypeError, ValueError) as error:
            raise HistoryUnavailable() from error

    def mark_reviewed(self, incident_id: int) -> bool:
        if type(incident_id) is not int or incident_id <= 0:
            raise ValueError("Identificador de incidente inválido.")
        if not self.path.exists():
            return False
        with self._connection(write=True) as connection:
            return connection.execute("UPDATE incidents SET reviewed=1 WHERE id=?", (incident_id,)).rowcount == 1


class ErrorHistoryHandler(logging.Handler):
    def __init__(self, history: ErrorHistory):
        super().__init__()
        self.history = history
        self.session = uuid.uuid4().hex
        self.write_failed = False
        self.dropped_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        fields = getattr(record, "diagnostic", None)
        if fields is None:
            return
        try:
            self.history.record(fields, self.session)
            self.write_failed = False
        except (HistoryUnavailable, ValueError, TypeError):
            self.dropped_count += 1
            if not self.write_failed and sys.stderr is not None:
                try:
                    sys.stderr.write("Historial de errores no disponible; el fallo pudo quedar sin guardar.\n")
                except (OSError, ValueError):
                    pass
            self.write_failed = True


def history_handler(logger: logging.Logger) -> ErrorHistoryHandler | None:
    return next((item for item in logger.handlers if isinstance(item, ErrorHistoryHandler)), None)
