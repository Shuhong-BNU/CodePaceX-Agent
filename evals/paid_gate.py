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

from evals.costing import PricingSnapshot, load_pricing, pricing_snapshot_hash

MONEY_QUANTUM = Decimal("0.000001")
BudgetStage = Literal["A", "B", "C"]
BudgetCategory = Literal[
    "swe", "mcp", "retention", "permission", "multi_agent", "long_session",
]
_CATEGORY_PREFIXES: dict[str, BudgetCategory] = {
    "swe": "swe", "mcp": "mcp", "retention": "retention",
    "permission": "permission", "multi": "multi_agent",
    "long_session": "long_session",
}


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


class BudgetBlock(BaseModel):
    """A pre-request refusal; it proves that no Provider call was permitted."""

    model_config = ConfigDict(extra="forbid")

    trial_id: str
    stage: BudgetStage
    category: BudgetCategory | None = None
    required_cny: Decimal = Field(gt=0)
    reason: Literal["stage_limit", "category_limit", "safety_reserve", "unknown_category"]
    recorded_at: str


class AuthorizationRebind(BaseModel):
    """Auditable authorization transition that preserves prior paid evidence."""

    model_config = ConfigDict(extra="forbid")

    previous_authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_allocation_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rebound_at: str


class BudgetLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    currency: Literal["CNY"] = "CNY"
    authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    spent_cny: Decimal = Field(default=Decimal("0"), ge=0)
    active_reservation: Reservation | None = None
    allocation_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    request_charges: list[RequestCharge] = Field(default_factory=list)
    settlements: list[Settlement] = Field(default_factory=list)
    budget_blocks: list[BudgetBlock] = Field(default_factory=list)
    authorization_rebinds: list[AuthorizationRebind] = Field(default_factory=list)
    updated_at: str


class StageCBudgetAllocation(BaseModel):
    """Commit-bound, non-transferable Stage C limits derived from prior Usage."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    currency: Literal["CNY"] = "CNY"
    experiment_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    pricing_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_authorization_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_spent_cny: Decimal = Field(ge=0)
    baseline_request_charge_count: int = Field(ge=0)
    baseline_settlement_count: int = Field(ge=0)
    baseline_budget_block_count: int = Field(default=0, ge=0)
    baseline_rebind_count: int = Field(ge=0)
    safety_reserve_cny: Decimal = Field(gt=0)
    spendable_total_cny: Decimal = Field(gt=0)
    category_limits_cny: dict[BudgetCategory, Decimal]

    @model_validator(mode="after")
    def validate_limits(self) -> StageCBudgetAllocation:
        if set(self.category_limits_cny) != set(_CATEGORY_PREFIXES.values()):
            raise ValueError("Stage C allocation requires every budget category")
        if min(self.category_limits_cny.values()) < 0:
            raise ValueError("Stage C category limits cannot be negative")
        if self.baseline_spent_cny + sum(self.category_limits_cny.values()) > self.spendable_total_cny:
            raise ValueError("Stage C category limits exceed the spendable total")
        return self


def authorization_hash(authorization: BudgetAuthorization) -> str:
    import hashlib

    payload = json.dumps(
        authorization.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def allocation_hash(allocation: StageCBudgetAllocation) -> str:
    import hashlib

    payload = json.dumps(
        allocation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def ledger_fingerprint(ledger: BudgetLedger) -> str:
    """Hash durable accounting facts, excluding volatile lock/allocation fields."""
    import hashlib

    payload = {
        "schema_version": ledger.schema_version,
        "currency": ledger.currency,
        "authorization_hash": ledger.authorization_hash,
        "spent_cny": str(ledger.spent_cny),
        "request_charges": [item.model_dump(mode="json") for item in ledger.request_charges],
        "settlements": [item.model_dump(mode="json") for item in ledger.settlements],
        "budget_blocks": [item.model_dump(mode="json") for item in ledger.budget_blocks],
        "authorization_rebinds": [
            item.model_dump(mode="json") for item in ledger.authorization_rebinds
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def stage_c_category(trial_id: str) -> BudgetCategory:
    prefix = trial_id.split("/", 1)[0]
    try:
        return _CATEGORY_PREFIXES[prefix]
    except KeyError as exc:
        raise ValueError(f"trial ID has no Stage C budget category: {trial_id}") from exc


def load_authorization(path: Path) -> BudgetAuthorization:
    return BudgetAuthorization.model_validate_json(path.read_text(encoding="utf-8"))


def load_stage_c_allocation(path: Path) -> StageCBudgetAllocation:
    return StageCBudgetAllocation.model_validate_json(path.read_text(encoding="utf-8"))


@contextmanager
def _exclusive_ledger_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
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
        lock_path.unlink(missing_ok=True)


def rebind_ledger_authorization(
    ledger_path: Path, *, previous: BudgetAuthorization, replacement: BudgetAuthorization,
) -> BudgetLedger:
    """Move a settled ledger to a new frozen commit without discarding costs."""
    with _exclusive_ledger_lock(ledger_path.with_suffix(ledger_path.suffix + ".lock")):
        ledger = BudgetLedger.model_validate_json(ledger_path.read_text(encoding="utf-8"))
        previous_hash = authorization_hash(previous)
        replacement_hash = authorization_hash(replacement)
        if ledger.authorization_hash != previous_hash:
            raise ValueError("budget ledger does not belong to the previous authorization")
        if ledger.active_reservation is not None:
            raise ValueError("cannot rebind a ledger with an active reservation")
        if previous.authorized_total_cny != replacement.authorized_total_cny:
            raise ValueError("authorization rebind requires the same total budget")
        if previous.stage_limits_cny != replacement.stage_limits_cny:
            raise ValueError("authorization rebind requires unchanged stage limits")
        if previous.pricing_snapshot_hash != replacement.pricing_snapshot_hash:
            raise ValueError("authorization rebind requires the same pricing snapshot")
        request_total = _money(sum(
            (item.actual_cny for item in ledger.request_charges), Decimal("0"),
        ))
        settlement_total = _money(sum(
            (item.actual_cny for item in ledger.settlements), Decimal("0"),
        ))
        if ledger.spent_cny != request_total or request_total != settlement_total:
            raise ValueError("cannot rebind an internally inconsistent budget ledger")
        ledger.authorization_hash = replacement_hash
        ledger.authorization_rebinds.append(AuthorizationRebind(
            previous_authorization_hash=previous_hash,
            authorization_hash=replacement_hash,
            previous_allocation_hash=ledger.allocation_hash,
            rebound_at=_utc_now(),
        ))
        # A Stage C allocation is commit-bound.  Settled charges remain durable
        # evidence, but the old allocation cannot authorize the new commit.
        ledger.allocation_hash = None
        ledger.updated_at = _utc_now()
        temporary = ledger_path.with_name(f".{ledger_path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(
                ledger.model_dump(mode="json"), ensure_ascii=False,
                indent=2, sort_keys=True,
            ) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, ledger_path)
        directory = os.open(ledger_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return ledger


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
    """Single-writer guard with one durable worst-next-request reservation."""

    def __init__(
        self, *, root: Path, authorization_path: Path, ledger_path: Path,
        pricing: PricingSnapshot, stage: BudgetStage,
        allocation_path: Path | None = None,
        pricing_path: Path | None = None,
    ) -> None:
        self.root = root.resolve()
        self.authorization_path = authorization_path.resolve()
        self.ledger_path = ledger_path.resolve()
        self.pricing_path = pricing_path.resolve() if pricing_path is not None else None
        self.allocation_path = allocation_path.resolve() if allocation_path is not None else None
        self.lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
        self.pricing = pricing
        self.stage = stage
        self._lock_depth = 0
        self.authorization = load_authorization(authorization_path)
        validate_authorization(self.authorization, root=self.root, pricing=pricing)
        self._authorization_hash = authorization_hash(self.authorization)
        self.allocation = (
            load_stage_c_allocation(self.allocation_path) if self.allocation_path is not None else None
        )
        self._allocation_hash = allocation_hash(self.allocation) if self.allocation else None
        if self.stage == "C" and self.allocation is None:
            raise ValueError("Stage C paid execution requires a budget allocation")
        if self.allocation is not None:
            if self.stage != "C":
                raise ValueError("budget allocation is only valid for Stage C")
            if self.allocation.experiment_commit != self.authorization.experiment_commit:
                raise ValueError("Stage C allocation is not bound to the authorization commit")
            if self.allocation.pricing_snapshot_hash != pricing_snapshot_hash(pricing):
                raise ValueError("Stage C allocation pricing snapshot hash mismatch")
            if self.allocation.spendable_total_cny + self.allocation.safety_reserve_cny > self.authorization.authorized_total_cny:
                raise ValueError("Stage C allocation consumes the reserved safety margin")

    @contextmanager
    def locked(self) -> Iterator[None]:
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        with _exclusive_ledger_lock(self.lock_path):
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0

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
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(
                ledger.model_dump(mode="json"), ensure_ascii=False,
                indent=2, sort_keys=True,
            ) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.ledger_path)
        directory = os.open(self.ledger_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _record_budget_block(
        self, ledger: BudgetLedger, *, trial_id: str, amount: Decimal,
        reason: Literal["stage_limit", "category_limit", "safety_reserve", "unknown_category"],
        category: BudgetCategory | None = None,
    ) -> None:
        ledger.budget_blocks.append(BudgetBlock(
            trial_id=trial_id, stage=self.stage, category=category,
            required_cny=amount, reason=reason, recorded_at=_utc_now(),
        ))
        self._write_ledger(ledger)

    def reserve(
        self, trial_id: str, *, maximum_requests: int,
        maximum_input_tokens_per_request: int,
        maximum_output_tokens_per_request: int,
    ) -> Reservation:
        """Reserve exactly one Provider request.

        The old trial-level interface remains named ``reserve`` for CLI caller
        compatibility, but a multi-request reservation is intentionally refused.
        Every next request must acquire its own reservation after the previous
        one has been settled.
        """
        if maximum_requests != 1:
            raise ValueError("paid reservations must cover exactly one Provider request")
        with self.locked():
            return self._reserve_request(
                trial_id, maximum_input_tokens_per_request=maximum_input_tokens_per_request,
                maximum_output_tokens_per_request=maximum_output_tokens_per_request,
            )

    def _reserve_request(
        self, trial_id: str, *, maximum_input_tokens_per_request: int,
        maximum_output_tokens_per_request: int,
    ) -> Reservation:
        validate_authorization(
            self.authorization, root=self.root, pricing=self.pricing,
        )
        ledger = self._load_ledger()
        if ledger.active_reservation is not None:
            raise ValueError("an unsettled paid trial reservation already exists")
        amount = worst_case_reservation(
            self.pricing, maximum_requests=1,
            maximum_input_tokens_per_request=maximum_input_tokens_per_request,
            maximum_output_tokens_per_request=maximum_output_tokens_per_request,
        )
        stage_limit = self.authorization.stage_limits_cny[self.stage]
        remaining = stage_limit - ledger.spent_cny
        if remaining < amount:
            self._record_budget_block(
                ledger, trial_id=trial_id, amount=amount, reason="stage_limit",
            )
            raise ValueError(
                f"insufficient stage {self.stage} budget for worst next request: "
                f"remaining={remaining} CNY required={amount} CNY"
            )
        if self.allocation is not None:
            self._validate_allocation_ledger(ledger)
            try:
                category = self._category_for_trial(trial_id)
            except ValueError:
                self._record_budget_block(
                    ledger, trial_id=trial_id, amount=amount, reason="unknown_category",
                )
                raise
            category_spent = sum(
                item.actual_cny
                for item in ledger.request_charges[
                    self.allocation.baseline_request_charge_count:
                ]
                if self._category_for_trial(item.trial_id) == category
            )
            if category_spent + amount > self.allocation.category_limits_cny[category]:
                self._record_budget_block(
                    ledger, trial_id=trial_id, amount=amount,
                    reason="category_limit", category=category,
                )
                raise ValueError(
                    f"insufficient Stage C {category} category budget for worst next request"
                )
            if ledger.spent_cny + amount > self.allocation.spendable_total_cny:
                self._record_budget_block(
                    ledger, trial_id=trial_id, amount=amount,
                    reason="safety_reserve", category=category,
                )
                raise ValueError("Stage C safety reserve prevents the worst next trial")
            ledger.allocation_hash = self._allocation_hash
        reservation = Reservation(
            reservation_id=uuid.uuid4().hex, trial_id=trial_id, stage=self.stage,
            maximum_requests=1,
            maximum_input_tokens_per_request=maximum_input_tokens_per_request,
            maximum_output_tokens_per_request=maximum_output_tokens_per_request,
            reserved_cny=amount, created_at=_utc_now(),
        )
        ledger.active_reservation = reservation
        self._write_ledger(ledger)
        return reservation

    def _validate_allocation_ledger(self, ledger: BudgetLedger) -> None:
        assert self.allocation is not None
        if len(ledger.request_charges) < self.allocation.baseline_request_charge_count:
            raise ValueError("Stage C allocation ledger baseline is ahead of the ledger")
        if len(ledger.settlements) < self.allocation.baseline_settlement_count:
            raise ValueError("Stage C allocation settlement baseline is ahead of the ledger")
        if len(ledger.budget_blocks) < self.allocation.baseline_budget_block_count:
            raise ValueError("Stage C allocation budget-block baseline is ahead of the ledger")
        if len(ledger.authorization_rebinds) < self.allocation.baseline_rebind_count:
            raise ValueError("Stage C allocation rebind baseline is ahead of the ledger")
        if ledger.authorization_hash != self.allocation.baseline_authorization_hash:
            raise ValueError("Stage C allocation authorization baseline changed")
        if ledger.allocation_hash not in {None, self._allocation_hash}:
            raise ValueError("Stage C allocation hash changed during resume")
        baseline = BudgetLedger(
            authorization_hash=ledger.authorization_hash,
            spent_cny=self.allocation.baseline_spent_cny,
            active_reservation=None,
            allocation_hash=None,
            request_charges=ledger.request_charges[:self.allocation.baseline_request_charge_count],
            settlements=ledger.settlements[:self.allocation.baseline_settlement_count],
            budget_blocks=ledger.budget_blocks[:self.allocation.baseline_budget_block_count],
            authorization_rebinds=ledger.authorization_rebinds[:self.allocation.baseline_rebind_count],
            updated_at=ledger.updated_at,
        )
        if ledger_fingerprint(baseline) != self.allocation.baseline_ledger_sha256:
            raise ValueError("Stage C allocation baseline ledger hash mismatch")

    @staticmethod
    def _category_for_trial(trial_id: str) -> BudgetCategory:
        return stage_c_category(trial_id)

    def settle(
        self, reservation: Reservation, *, request_usages: list[tuple[int, int]],
    ) -> Settlement:
        if len(request_usages) != 1:
            raise ValueError("each paid reservation must settle exactly one Provider request")
        with self.locked():
            return self._settle_request(reservation, request_usages[0])

    def _settle_request(
        self, reservation: Reservation, request_usage: tuple[int, int],
    ) -> Settlement:
        ledger = self._load_ledger()
        active = ledger.active_reservation
        if active is None or active.reservation_id != reservation.reservation_id:
            raise ValueError("paid trial reservation is not active")
        input_tokens, output_tokens = request_usage
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("provider request token usage cannot be negative")
        request_index = 1 + sum(
            item.trial_id == active.trial_id for item in ledger.request_charges
        )
        if any(
            item.reservation_id == active.reservation_id for item in ledger.request_charges
        ) or any(
            item.reservation_id == active.reservation_id for item in ledger.settlements
        ):
            raise ValueError("reservation ID has already been settled")
        charge = RequestCharge(
            reservation_id=active.reservation_id, trial_id=active.trial_id,
            request_index=request_index, input_tokens=input_tokens,
            output_tokens=output_tokens,
            actual_cny=actual_cost(
                self.pricing, input_tokens=input_tokens, output_tokens=output_tokens,
            ), recorded_at=_utc_now(),
        )
        cost = charge.actual_cny
        if cost > active.reserved_cny:
            raise ValueError("observed token cost exceeded the reserved ceiling")
        settlement = Settlement(
            reservation_id=active.reservation_id, trial_id=active.trial_id,
            stage=active.stage,
            requests=1, input_tokens=input_tokens,
            output_tokens=output_tokens, actual_cny=cost,
            status="settled", settled_at=_utc_now(),
        )
        ledger.spent_cny = _money(ledger.spent_cny + cost)
        ledger.request_charges.append(charge)
        ledger.settlements.append(settlement)
        ledger.active_reservation = None
        self._write_ledger(ledger)
        return settlement

    def cancel(self, reservation: Reservation) -> Settlement:
        with self.locked():
            return self._cancel(reservation)

    def _cancel(self, reservation: Reservation) -> Settlement:
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

    def trial_accounting(self, trial_id: str) -> dict[str, Any]:
        """Read durable per-request accounting for a completed or blocked Trial."""
        with self.locked():
            ledger = self._load_ledger()
            charges = [item for item in ledger.request_charges if item.trial_id == trial_id]
            settlements = [item for item in ledger.settlements if item.trial_id == trial_id]
            blocks = [item for item in ledger.budget_blocks if item.trial_id == trial_id]
            return {
                "request_count": len(charges),
                "actual_cny": str(_money(sum(
                    (item.actual_cny for item in charges), Decimal("0"),
                ))),
                "reservation_ids": [item.reservation_id for item in charges],
                "budget_blocked": bool(blocks),
                "budget_block_reasons": [item.reason for item in blocks],
                "active_reservation": (
                    ledger.active_reservation.model_dump(mode="json")
                    if ledger.active_reservation is not None
                    and ledger.active_reservation.trial_id == trial_id else None
                ),
                "settlement_count": len(settlements),
            }

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


_REQUEST_BUDGET_ENV = "CODEPACEX_EXPERIMENT_REQUEST_BUDGET"
_REQUEST_BUDGET_KEYS = (
    _REQUEST_BUDGET_ENV,
    "CODEPACEX_BUDGET_ROOT",
    "CODEPACEX_BUDGET_AUTHORIZATION",
    "CODEPACEX_BUDGET_LEDGER",
    "CODEPACEX_BUDGET_ALLOCATION",
    "CODEPACEX_BUDGET_PRICING",
    "CODEPACEX_BUDGET_STAGE",
    "CODEPACEX_BUDGET_TRIAL_ID",
    "CODEPACEX_BUDGET_MAX_INPUT_TOKENS",
    "CODEPACEX_BUDGET_MAX_OUTPUT_TOKENS",
)


def provider_request_budget_environment(
    gate: PaidRunGate, *, trial_id: str,
    maximum_input_tokens_per_request: int,
    maximum_output_tokens_per_request: int,
) -> dict[str, str]:
    """Return the explicit, non-secret environment contract for one Trial.

    The child process obtains this contract only for a frozen paid experiment.
    Normal CodePaceX usage has no such variables and never imports this guard.
    """
    if not trial_id:
        raise ValueError("a paid provider request requires a trial ID")
    if gate.pricing_path is None:
        raise ValueError("Provider request budget requires a frozen pricing path")
    if min(maximum_input_tokens_per_request, maximum_output_tokens_per_request) <= 0:
        raise ValueError("positive per-request token ceilings are required")
    return {
        _REQUEST_BUDGET_ENV: "1",
        "CODEPACEX_BUDGET_ROOT": str(gate.root),
        "CODEPACEX_BUDGET_AUTHORIZATION": str(gate.authorization_path.resolve()),
        "CODEPACEX_BUDGET_LEDGER": str(gate.ledger_path.resolve()),
        "CODEPACEX_BUDGET_ALLOCATION": (
            str(gate.allocation_path.resolve()) if gate.allocation_path is not None else ""
        ),
        "CODEPACEX_BUDGET_PRICING": str(gate.pricing_path),
        "CODEPACEX_BUDGET_STAGE": gate.stage,
        "CODEPACEX_BUDGET_TRIAL_ID": trial_id,
        "CODEPACEX_BUDGET_MAX_INPUT_TOKENS": str(maximum_input_tokens_per_request),
        "CODEPACEX_BUDGET_MAX_OUTPUT_TOKENS": str(maximum_output_tokens_per_request),
    }


@contextmanager
def provider_request_budget_scope(
    gate: PaidRunGate, *, trial_id: str,
    maximum_input_tokens_per_request: int,
    maximum_output_tokens_per_request: int,
) -> Iterator[None]:
    """Temporarily bind an in-process experiment request to its Trial."""
    updates = provider_request_budget_environment(
        gate, trial_id=trial_id,
        maximum_input_tokens_per_request=maximum_input_tokens_per_request,
        maximum_output_tokens_per_request=maximum_output_tokens_per_request,
    )
    previous = {key: os.environ.get(key) for key in _REQUEST_BUDGET_KEYS}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ProviderRequestBudget:
    """Child/in-process bridge used immediately around one Provider request."""

    def __init__(self, gate: PaidRunGate, *, trial_id: str,
                 maximum_input_tokens_per_request: int,
                 maximum_output_tokens_per_request: int) -> None:
        self.gate = gate
        self.trial_id = trial_id
        self.maximum_input_tokens_per_request = maximum_input_tokens_per_request
        self.maximum_output_tokens_per_request = maximum_output_tokens_per_request

    @classmethod
    def from_environment(cls) -> ProviderRequestBudget | None:
        enabled = os.environ.get(_REQUEST_BUDGET_ENV)
        if enabled is None:
            return None
        if enabled != "1":
            raise ValueError("invalid experimental Provider request budget switch")
        required = {
            key: os.environ.get(key, "")
            for key in _REQUEST_BUDGET_KEYS if key != _REQUEST_BUDGET_ENV
        }
        missing = [key for key, value in required.items()
                   if not value and key != "CODEPACEX_BUDGET_ALLOCATION"]
        if missing:
            raise ValueError("incomplete experimental Provider request budget contract")
        allocation = required["CODEPACEX_BUDGET_ALLOCATION"] or None
        gate = PaidRunGate(
            root=Path(required["CODEPACEX_BUDGET_ROOT"]),
            authorization_path=Path(required["CODEPACEX_BUDGET_AUTHORIZATION"]),
            ledger_path=Path(required["CODEPACEX_BUDGET_LEDGER"]),
            pricing=load_pricing(Path(required["CODEPACEX_BUDGET_PRICING"])),
            stage=required["CODEPACEX_BUDGET_STAGE"],  # type: ignore[arg-type]
            allocation_path=Path(allocation) if allocation else None,
        )
        return cls(
            gate, trial_id=required["CODEPACEX_BUDGET_TRIAL_ID"],
            maximum_input_tokens_per_request=int(
                required["CODEPACEX_BUDGET_MAX_INPUT_TOKENS"]
            ),
            maximum_output_tokens_per_request=int(
                required["CODEPACEX_BUDGET_MAX_OUTPUT_TOKENS"]
            ),
        )

    def reserve_before_request(self) -> Reservation:
        return self.gate.reserve(
            self.trial_id, maximum_requests=1,
            maximum_input_tokens_per_request=self.maximum_input_tokens_per_request,
            maximum_output_tokens_per_request=self.maximum_output_tokens_per_request,
        )

    def settle_after_usage(
        self, reservation: Reservation, provider_usage: Mapping[str, Any] | None,
    ) -> Settlement:
        if not isinstance(provider_usage, Mapping):
            raise ProviderUsageUnknown(
                "Provider Usage is missing; active reservation retained for reconciliation"
            )
        input_tokens, output_tokens = billable_request_usage({
            "request_input_tokens": 0,
            "request_output_tokens": 0,
            "provider_usage": provider_usage,
        })
        return self.gate.settle(
            reservation, request_usages=[(input_tokens, output_tokens)],
        )


class ProviderUsageUnknown(RuntimeError):
    """A request may have been billed but has no durable Provider Usage."""
