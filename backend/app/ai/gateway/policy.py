"""What may leave, in what form, and to which provider.

Three data tiers, ordered:

    PUBLIC      no bank data at all: market rates, news, the user's question
                with nothing of the bank's in it.
    AGGREGATE   the bank's aggregated figures, with every identifying field
                tokenised or dropped and amounts blurred.
    RESTRICTED  aggregated figures with an identifying field in clear (an
                administrator set branch names to "pass"). Only for a provider
                the bank controls -- on-premise, or under a contract that
                allows it.

Account-level data has no tier: it never leaves, whatever the settings say.
That is `HARD_DROP`, and `FieldPolicy` refuses to be configured otherwise.

A payload's tier is derived from the policy that built it, not declared by the
caller, so a caller cannot understate what it is sending.

PURE: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from app.ai.gateway.generalize import AmountMode


class Tier(IntEnum):
    PUBLIC = 0
    AGGREGATE = 1
    RESTRICTED = 2


class PolicyViolation(Exception):
    """A request the policy forbids. Carries a reason fit for the egress log."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


#: Fields that never leave in any form. Not configurable.
HARD_DROP = frozenset({"account_no", "customer", "user", "employee_id", "udf"})

#: Configurable fields -> the actions an administrator may choose.
ALLOWED: dict[str, tuple[str, ...]] = {
    "branch": ("token", "drop", "pass"),
    "district": ("token", "drop", "pass"),
    "division": ("token", "drop", "pass"),
    "branch_category": ("pass", "drop"),
    #: Generic product types ("TERM DEPOSIT - 3 MONTHS") exist at every bank
    #: and identify no one; the model needs them to match a tenor to a market
    #: rate. The internal product code is the bank's own and is dropped.
    "product_name": ("pass", "token"),
    "product_code": ("drop", "token"),
}

DEFAULTS: dict[str, str] = {
    "branch": "token",
    "district": "token",
    "division": "token",
    "branch_category": "pass",
    "product_name": "pass",
    "product_code": "drop",
}

#: Fields whose clear-text form identifies a unit of the bank.
IDENTIFYING = frozenset({"branch", "district", "division"})


@dataclass(frozen=True)
class FieldPolicy:
    actions: dict[str, str] = field(default_factory=lambda: dict(DEFAULTS))
    amount_mode: AmountMode = AmountMode.CRORE_3SF

    @classmethod
    def from_dict(cls, d: dict | None) -> "FieldPolicy":
        d = d or {}
        actions = dict(DEFAULTS)
        for k, v in (d.get("actions") or {}).items():
            if k in HARD_DROP:
                raise PolicyViolation(
                    "hard_drop", f"{k} never leaves the bank and cannot be configured")
            if k not in ALLOWED:
                raise PolicyViolation("unknown_field", f"unknown field {k!r}")
            if v not in ALLOWED[k]:
                raise PolicyViolation(
                    "bad_action", f"{k} may be {', '.join(ALLOWED[k])}, not {v!r}")
            actions[k] = v
        mode = AmountMode(d.get("amount_mode", AmountMode.CRORE_3SF.value))
        return cls(actions, mode)

    def to_dict(self) -> dict:
        return {"actions": dict(self.actions), "amount_mode": self.amount_mode.value}

    def action(self, field_name: str) -> str:
        if field_name in HARD_DROP:
            return "drop"
        return self.actions.get(field_name, "drop")

    @property
    def bank_tier(self) -> Tier:
        """The tier of any bank payload built under this policy."""
        if any(self.actions.get(f) == "pass" for f in IDENTIFYING):
            return Tier.RESTRICTED
        return Tier.AGGREGATE


def payload_tier(segment_kinds: set[str], policy: FieldPolicy) -> Tier:
    """The tier of a request from the kinds of segment it carries.

    A user's typed question counts as bank data: people name branches.
    """
    if segment_kinds & {"bank", "user"}:
        return policy.bank_tier
    return Tier.PUBLIC


def check_provider(payload: Tier, provider_max: Tier) -> None:
    if payload > provider_max:
        raise PolicyViolation(
            "tier_exceeded",
            f"this request carries {payload.name} data; the active provider is "
            f"cleared for {provider_max.name} only",
        )
