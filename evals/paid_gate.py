"""Fail-closed authorization and accounting for Goal 2 paid provider calls."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_UP
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.costing import PricingSnapshot, pricing_snapshot_hash

MONEY_QUANTUM = Decimal("0.000001")
BudgetStage = Literal["A", "B", "C"]


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_UP)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BudgetAuthorization(BaseModel):
    """User-created authorization bound to one frozen commit and price sheet."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    currency: Literal["CNY"] = "CNY"
    authorized_total_cny: Decimal = Field(gt=0)
    stage_limits_cny: dict[BudgetStage, Decimal]
    pricing_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    authorized_at: str
    authorized_by: Literal["user"] = "user"

    @model_validator(mode="after")
    def validate_stage_limits(self) -> BudgetAuthorization:
        if set(self.stage_limits_cny) != {"A", "B", "C"}:
            raise ValueError("budget authorization requires A, B, and C stage limits")
        stage_a = self.stage_limits_cny["A"]
        stage_b = self.stage_limits_cny["B"]
        stage_c = self.stage_limits_cny["C"]
        if min(stage_a, stage_b, stage_c) <= 0 or not stage_a <= stage_b <= stage_c:
            raise ValueError("budget stage limits must be positive and monotonic")
        if stage_c != self.authorized_total_cny:
            raise ValueError("stage C limit must equal the total authorization")
        return self


class Reservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    trial_id: str
    stage: BudgetStage
    maximum_requests: int = Field(gt=0)
    maximum_input_tokens_per_request: int = Field(gt=0)
    maximum_output_tokens_per_request: int = Field(gt=0)
    reserved_cny: Decimal = Field(gt=0)
    created_at: str


class Settlement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    trial_id: str
    stage: BudgetStage
    requests: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    actual_cny: Decimal = Field(ge=0)
    status: Literal["settled", "cancelled"]
    settled_at: str


class RequestCharge(BaseModel):
    """One auditable provider request recorded before trial settlement."""

    model_config = ConfigDict(extra="forbid")

    reservation_id: str
    trial_id: str
    request_index: int = Field(gt=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    actual_cny: Decimal = Field(ge=0)
    recorded_at: str


class BudgetLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    currency: Literal["CNY"] = "CNY"
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spent_cny: Decimal = Field(default=Decimal("0"), ge=0)
    active_reservation: Reservation | None = None
    request_charges: list[RequestCharge] = Field(default_factory=list)
    settlements: list[Settlement] = Field(default_factory=list)
    updated_at: str


def authorization_hash(authorization: BudgetAuthorization) -> str:
    import hashlib

    payload = json.dumps(
        authorization.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_authorization(path: Path) -> BudgetAuthorization:
    return BudgetAuthorization.model_validate_json(path.read_text(encoding="utf-8"))


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or len(commit) != 40:
        raise ValueError("cannot identify frozen experiment commit")
    return commit


def _git_is_clean(root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        text=True, capture_output=True, check=False,
    )
    return result.returncode == 0 and not result.stdout


def validate_authorization(
    authorization: BudgetAuthorization, *, root: Path, pricing: PricingSnapshot,
) -> None:
    if authorization.pricing_snapshot_hash != pricing_snapshot_hash(pricing):
        raise ValueError("budget authorization pricing snapshot hash mismatch")
    if authorization.experiment_commit != _git_commit(root):
        raise ValueError("budget authorization is not bound to current HEAD")
    if not _git_is_clean(root):
        raise ValueError("paid execution requires a clean frozen Git worktree")


def worst_case_reservation(
    pricing: PricingSnapshot, *, maximum_requests: int,
    maximum_input_tokens_per_request: int,
    maximum_output_tokens_per_request: int,
) -> Decimal:
    if min(
        maximum_requests, maximum_input_tokens_per_request,
        maximum_output_tokens_per_request,
    ) <= 0:
        raise ValueError("positive request and token ceilings are required")
    cost = Decimal(maximum_requests) * (
        Decimal(maximum_input_tokens_per_request)
        * Decimal(str(pricing.input_price)) / Decimal(pricing.unit_tokens)
        + Decimal(maximum_output_tokens_per_request)
        * Decimal(str(pricing.output_price)) / Decimal(pricing.unit_tokens)
    )
    return _money(cost)


def actual_cost(
    pricing: PricingSnapshot, *, input_tokens: int, output_tokens: int,
) -> Decimal:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token usage cannot be negative")
    return _money(
        Decimal(input_tokens) * Decimal(str(pricing.input_price))
        / Decimal(pricing.unit_tokens)
        + Decimal(output_tokens) * Decimal(str(pricing.output_price))
        / Decimal(pricing.unit_tokens)
    )


def billable_request_usage(event: Mapping[str, Any]) -> tuple[int, int]:
    """Return total provider tokens for frozen pricing without cache discounts."""
    if "request_input_tokens" not in event or "request_output_tokens" not in event:
        raise ValueError("provider usage lacks per-request token accounting")
    request_input = int(event.get("request_input_tokens") or 0)
    request_output = int(event.get("request_output_tokens") or 0)
    raw = event.get("provider_usage")
    if isinstance(raw, Mapping):
        for key in ("prompt_tokens", "input_tokens"):
            if key in raw and raw[key] is not None:
                request_input = int(raw[key])
                break
        for key in ("completion_tokens", "output_tokens"):
            if key in raw and raw[key] is not None:
                request_output = int(raw[key])
                break
    if request_input < 0 or request_output < 0:
        raise ValueError("provider request token usage cannot be negative")
    return request_input, request_output


class PaidRunGate:
    """Single-writer budget guard with a durable worst-next-trial reservation."""

    def __init__(
        self, *, root: Path, authorization_path: Path, ledger_path: Path,
        pricing: PricingSnapshot, stage: BudgetStage,
    ) -> None:
        self.root = root.resolve()
        self.authorization_path = authorization_path
        self.ledger_path = ledger_path
        self.lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        self.pricing = pricing
        self.stage = stage
        self.authorization = load_authorization(authorization_path)
        validate_authorization(self.authorization, root=self.root, pricing=pricing)
        self._authorization_hash = authorization_hash(self.authorization)

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
            )
        except FileExistsError as exc:
            raise ValueError("another paid Goal 2 process holds the budget lock") from exc
        try:
            os.write(descriptor, f"pid={os.getpid()}\n".encode())
            os.close(descriptor)
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self.lock_path.unlink(missing_ok=True)

    def _load_ledger(self) -> BudgetLedger:
        if not self.ledger_path.exists():
            return BudgetLedger(
                authorization_hash=self._authorization_hash, updated_at=_utc_now(),
            )
        ledger = BudgetLedger.model_validate_json(
            self.ledger_path.read_text(encoding="utf-8"),
        )
        if ledger.authorization_hash != self._authorization_hash:
            raise ValueError("budget ledger belongs to a different authorization")
        return ledger

    def _write_ledger(self, ledger: BudgetLedger) -> None:
        ledger.updated_at = _utc_now()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ledger_path.with_name(
            f".{self.ledger_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(
                ledger.model_dump(mode="json"), ensure_ascii=False,
                indent=2, sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.ledger_path)

    def reserve(
        self, trial_id: str, *, maximum_requests: int,
        maximum_input_tokens_per_request: int,
        maximum_output_tokens_per_request: int,
    ) -> Reservation:
        validate_authorization(
            self.authorization, root=self.root, pricing=self.pricing,
        )
        ledger = self._load_ledger()
        if ledger.active_reservation is not None:
            raise ValueError("an unsettled paid trial reservation already exists")
        amount = worst_case_reservation(
            self.pricing, maximum_requests=maximum_requests,
            maximum_input_tokens_per_request=maximum_input_tokens_per_request,
            maximum_output_tokens_per_request=maximum_output_tokens_per_request,
        )
        stage_limit = self.authorization.stage_limits_cny[self.stage]
        remaining = stage_limit - ledger.spent_cny
        if remaining < amount:
            raise ValueError(
                f"insufficient stage {self.stage} budget for worst next trial: "
                f"remaining={remaining} CNY required={amount} CNY"
            )
        reservation = Reservation(
            reservation_id=uuid.uuid4().hex, trial_id=trial_id, stage=self.stage,
            maximum_requests=maximum_requests,
            maximum_input_tokens_per_request=maximum_input_tokens_per_request,
            maximum_output_tokens_per_request=maximum_output_tokens_per_request,
            reserved_cny=amount, created_at=_utc_now(),
        )
        ledger.active_reservation = reservation
        self._write_ledger(ledger)
        return reservation

    def settle(
        self, reservation: Reservation, *, request_usages: list[tuple[int, int]],
    ) -> Settlement:
        ledger = self._load_ledger()
        active = ledger.active_reservation
        if active is None or active.reservation_id != reservation.reservation_id:
            raise ValueError("paid trial reservation is not active")
        requests = len(request_usages)
        if requests > active.maximum_requests:
            raise ValueError("provider request count exceeded the reserved ceiling")
        if not request_usages:
            raise ValueError("paid trial settlement requires provider request usage")
        request_charges: list[RequestCharge] = []
        for request_index, (input_tokens, output_tokens) in enumerate(request_usages, 1):
            if input_tokens < 0 or output_tokens < 0:
                raise ValueError("provider request token usage cannot be negative")
            # Provider Usage can include reasoning tokens beyond the requested
            # output limit. Record the observed values exactly; the aggregate
            # reservation check below remains the fail-closed budget boundary.
            request_charges.append(RequestCharge(
                reservation_id=active.reservation_id,
                trial_id=active.trial_id,
                request_index=request_index,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                actual_cny=actual_cost(
                    self.pricing, input_tokens=input_tokens, output_tokens=output_tokens,
                ),
                recorded_at=_utc_now(),
            ))
        input_tokens = sum(item.input_tokens for item in request_charges)
        output_tokens = sum(item.output_tokens for item in request_charges)
        cost = actual_cost(
            self.pricing, input_tokens=input_tokens, output_tokens=output_tokens,
        )
        if cost > active.reserved_cny:
            raise ValueError("observed token cost exceeded the reserved ceiling")
        settlement = Settlement(
            reservation_id=active.reservation_id, trial_id=active.trial_id,
            stage=active.stage,
            requests=requests, input_tokens=input_tokens,
            output_tokens=output_tokens, actual_cny=cost,
            status="settled", settled_at=_utc_now(),
        )
        ledger.spent_cny = _money(ledger.spent_cny + cost)
        ledger.request_charges.extend(request_charges)
        ledger.settlements.append(settlement)
        ledger.active_reservation = None
        self._write_ledger(ledger)
        return settlement

    def cancel(self, reservation: Reservation) -> Settlement:
        ledger = self._load_ledger()
        active = ledger.active_reservation
        if active is None or active.reservation_id != reservation.reservation_id:
            raise ValueError("paid trial reservation is not active")
        settlement = Settlement(
            reservation_id=active.reservation_id, trial_id=active.trial_id,
            stage=active.stage,
            requests=0, input_tokens=0, output_tokens=0,
            actual_cny=Decimal("0"), status="cancelled", settled_at=_utc_now(),
        )
        ledger.settlements.append(settlement)
        ledger.active_reservation = None
        self._write_ledger(ledger)
        return settlement

    def summary(self) -> dict[str, str | int | bool]:
        ledger = self._load_ledger()
        total = self.authorization.authorized_total_cny
        return {
            "currency": "CNY",
            "stage": self.stage,
            "stage_limit_cny": str(self.authorization.stage_limits_cny[self.stage]),
            "authorized_total_cny": str(total),
            "spent_cny": str(ledger.spent_cny),
            "remaining_cny": str(total - ledger.spent_cny),
            "settlement_count": len(ledger.settlements),
            "request_charge_count": len(ledger.request_charges),
            "active_reservation": ledger.active_reservation is not None,
        }
