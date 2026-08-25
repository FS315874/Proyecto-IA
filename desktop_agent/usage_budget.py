import json
import math
import os
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from desktop_agent.interpretation import (
    MonthlyUsageSnapshot,
    ProposalBudgetExceededError,
    ProposalProviderResult,
    ProposalUsage,
)

USAGE_FILE_SCHEMA_VERSION = 1

# Una solicitud de este adaptador tiene entrada acotada, no usa herramientas y limita
# la salida a 128 tokens. Reservar un centavo deja un margen amplio frente a su costo
# esperado y evita iniciar una llamada cuando el saldo mensual es insuficiente.
CALL_RESERVATION_USD = 0.01

_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_RECORD_FIELDS = frozenset(
    {
        "request_count",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "settled_cost_usd",
        "unmetered_request_count",
        "pending_reservations",
    }
)

MonthProvider = Callable[[], str]
ReservationIdFactory = Callable[[], str]


class UsageLedgerError(RuntimeError):
    """El registro local no puede leerse o actualizarse de forma segura."""


def _current_local_month() -> str:
    return datetime.now().astimezone().strftime("%Y-%m")


def _default_usage_file() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data)
        if candidate.is_absolute():
            return candidate / "DesktopAgent" / "ai_usage.json"
    return Path.home() / ".desktop_agent" / "ai_usage.json"


DEFAULT_USAGE_FILE = _default_usage_file()


def _empty_record() -> dict[str, object]:
    return {
        "request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_tokens": 0,
        "settled_cost_usd": 0.0,
        "unmetered_request_count": 0,
        "pending_reservations": {},
    }


def _require_non_negative_int(value: object) -> int:
    if type(value) is not int or value < 0:
        raise UsageLedgerError("El registro local de uso es inválido.")
    return value


def _require_non_negative_money(value: object) -> float:
    if type(value) not in (int, float):
        raise UsageLedgerError("El registro local de uso es inválido.")
    money = float(value)
    if not math.isfinite(money) or money < 0:
        raise UsageLedgerError("El registro local de uso es inválido.")
    return money


def _validate_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise UsageLedgerError("El registro local de uso es inválido.")

    request_count = _require_non_negative_int(value["request_count"])
    input_tokens = _require_non_negative_int(value["input_tokens"])
    output_tokens = _require_non_negative_int(value["output_tokens"])
    total_tokens = _require_non_negative_int(value["total_tokens"])
    cached_input_tokens = _require_non_negative_int(
        value["cached_input_tokens"]
    )
    cache_write_tokens = _require_non_negative_int(value["cache_write_tokens"])
    unmetered_request_count = _require_non_negative_int(
        value["unmetered_request_count"]
    )
    settled_cost_usd = _require_non_negative_money(value["settled_cost_usd"])

    if total_tokens != input_tokens + output_tokens:
        raise UsageLedgerError("El registro local de uso es inválido.")
    if cached_input_tokens + cache_write_tokens > input_tokens:
        raise UsageLedgerError("El registro local de uso es inválido.")
    if unmetered_request_count > request_count:
        raise UsageLedgerError("El registro local de uso es inválido.")

    raw_pending = value["pending_reservations"]
    if not isinstance(raw_pending, dict):
        raise UsageLedgerError("El registro local de uso es inválido.")
    pending: dict[str, float] = {}
    for reservation_id, amount in raw_pending.items():
        if not isinstance(reservation_id, str) or not reservation_id:
            raise UsageLedgerError("El registro local de uso es inválido.")
        pending[reservation_id] = _require_non_negative_money(amount)

    if len(pending) > request_count:
        raise UsageLedgerError("El registro local de uso es inválido.")

    return {
        "request_count": request_count,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
        "settled_cost_usd": settled_cost_usd,
        "unmetered_request_count": unmetered_request_count,
        "pending_reservations": pending,
    }


class MonthlyUsageLedger:
    """Persiste uso agregado por mes sin conservar órdenes ni respuestas."""

    def __init__(
        self,
        monthly_budget_usd: float,
        path: Path = DEFAULT_USAGE_FILE,
        month_provider: MonthProvider = _current_local_month,
        reservation_id_factory: ReservationIdFactory | None = None,
    ) -> None:
        if (
            type(monthly_budget_usd) not in (int, float)
            or not math.isfinite(float(monthly_budget_usd))
            or float(monthly_budget_usd) <= 0
        ):
            raise ValueError("El presupuesto mensual debe ser un número positivo.")
        if not isinstance(path, Path):
            raise TypeError("La ruta del registro debe ser Path.")

        self._monthly_budget_usd = float(monthly_budget_usd)
        self._path = path
        self._month_provider = month_provider
        self._reservation_id_factory = (
            reservation_id_factory or (lambda: uuid.uuid4().hex)
        )

    def current_snapshot(self) -> MonthlyUsageSnapshot:
        state = self._load_state()
        month = self._read_month()
        record = state["months"].get(month, _empty_record())
        assert isinstance(record, dict)
        return self._snapshot(month, record)

    def reserve(self) -> tuple[str, MonthlyUsageSnapshot]:
        state = self._load_state()
        month = self._read_month()
        months = state["months"]
        assert isinstance(months, dict)
        record = months.setdefault(month, _empty_record())
        assert isinstance(record, dict)

        snapshot = self._snapshot(month, record)
        if snapshot.remaining_usd + 1e-12 < CALL_RESERVATION_USD:
            raise ProposalBudgetExceededError(
                "El presupuesto mensual de IA no permite otra llamada.",
                ProposalProviderResult(
                    payload=None,
                    monthly_usage=snapshot,
                ),
            )

        reservation_id = self._reservation_id_factory()
        if not isinstance(reservation_id, str) or not reservation_id:
            raise UsageLedgerError("No se pudo crear una reserva de presupuesto.")

        pending = record["pending_reservations"]
        assert isinstance(pending, dict)
        if reservation_id in pending:
            raise UsageLedgerError("No se pudo crear una reserva de presupuesto.")
        pending[reservation_id] = CALL_RESERVATION_USD
        record["request_count"] = int(record["request_count"]) + 1
        self._save_state(state)
        return reservation_id, self._snapshot(month, record)

    def settle(
        self,
        reservation_id: str,
        usage: ProposalUsage | None,
        estimated_cost_usd: float | None,
    ) -> MonthlyUsageSnapshot:
        state = self._load_state()
        months = state["months"]
        assert isinstance(months, dict)

        matching_month: str | None = None
        matching_record: dict[str, object] | None = None
        reservation_amount: float | None = None
        for month, record in months.items():
            assert isinstance(month, str)
            assert isinstance(record, dict)
            pending = record["pending_reservations"]
            assert isinstance(pending, dict)
            if reservation_id in pending:
                if matching_month is not None:
                    raise UsageLedgerError("El registro local de uso es inválido.")
                matching_month = month
                matching_record = record
                reservation_amount = float(pending[reservation_id])

        if (
            matching_month is None
            or matching_record is None
            or reservation_amount is None
        ):
            raise UsageLedgerError("La reserva de presupuesto no existe.")

        pending = matching_record["pending_reservations"]
        assert isinstance(pending, dict)
        del pending[reservation_id]

        cost_is_metered = (
            estimated_cost_usd is not None
            and type(estimated_cost_usd) in (int, float)
            and math.isfinite(float(estimated_cost_usd))
            and float(estimated_cost_usd) >= 0
        )
        if usage is not None and cost_is_metered:
            matching_record["input_tokens"] = (
                int(matching_record["input_tokens"]) + usage.input_tokens
            )
            matching_record["output_tokens"] = (
                int(matching_record["output_tokens"]) + usage.output_tokens
            )
            matching_record["total_tokens"] = (
                int(matching_record["total_tokens"]) + usage.total_tokens
            )
            matching_record["cached_input_tokens"] = (
                int(matching_record["cached_input_tokens"])
                + usage.cached_input_tokens
            )
            matching_record["cache_write_tokens"] = (
                int(matching_record["cache_write_tokens"])
                + usage.cache_write_tokens
            )
            charged_cost = float(estimated_cost_usd)
        else:
            matching_record["unmetered_request_count"] = (
                int(matching_record["unmetered_request_count"]) + 1
            )
            charged_cost = reservation_amount

        matching_record["settled_cost_usd"] = round(
            float(matching_record["settled_cost_usd"]) + charged_cost,
            12,
        )
        self._save_state(state)
        return self._snapshot(matching_month, matching_record)

    def _read_month(self) -> str:
        month = self._month_provider()
        if not isinstance(month, str) or _MONTH_PATTERN.fullmatch(month) is None:
            raise UsageLedgerError("El mes del registro local no es válido.")
        return month

    def _load_state(self) -> dict[str, object]:
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"schema_version": USAGE_FILE_SCHEMA_VERSION, "months": {}}
        except OSError as error:
            raise UsageLedgerError(
                "No se pudo leer el registro local de uso."
            ) from error

        try:
            raw_state = json.loads(raw_text)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise UsageLedgerError("El registro local de uso es inválido.") from error

        if (
            not isinstance(raw_state, dict)
            or set(raw_state) != {"schema_version", "months"}
            or raw_state["schema_version"] != USAGE_FILE_SCHEMA_VERSION
            or not isinstance(raw_state["months"], dict)
        ):
            raise UsageLedgerError("El registro local de uso es inválido.")

        months: dict[str, dict[str, object]] = {}
        for month, record in raw_state["months"].items():
            if not isinstance(month, str) or _MONTH_PATTERN.fullmatch(month) is None:
                raise UsageLedgerError("El registro local de uso es inválido.")
            months[month] = _validate_record(record)

        return {"schema_version": USAGE_FILE_SCHEMA_VERSION, "months": months}

    def _save_state(self, state: dict[str, object]) -> None:
        temporary_path = self._path.with_name(f"{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(state, file, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._path)
        except OSError as error:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise UsageLedgerError(
                "No se pudo actualizar el registro local de uso."
            ) from error

    def _snapshot(
        self,
        month: str,
        record: dict[str, object],
    ) -> MonthlyUsageSnapshot:
        pending = record["pending_reservations"]
        assert isinstance(pending, dict)
        accounted_cost = round(
            float(record["settled_cost_usd"])
            + math.fsum(float(amount) for amount in pending.values()),
            12,
        )
        return MonthlyUsageSnapshot(
            month=month,
            request_count=int(record["request_count"]),
            input_tokens=int(record["input_tokens"]),
            output_tokens=int(record["output_tokens"]),
            total_tokens=int(record["total_tokens"]),
            cached_input_tokens=int(record["cached_input_tokens"]),
            cache_write_tokens=int(record["cache_write_tokens"]),
            estimated_cost_usd=accounted_cost,
            budget_usd=self._monthly_budget_usd,
            remaining_usd=max(
                0.0,
                round(self._monthly_budget_usd - accounted_cost, 12),
            ),
            unmetered_request_count=int(record["unmetered_request_count"]),
            pending_reservation_count=len(pending),
        )
