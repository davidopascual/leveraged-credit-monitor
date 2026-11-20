"""Recovery waterfall. Pure functions: numbers in, numbers out. No I/O.

Steps
1. Enterprise value (EV) = default-scenario EBITDA x distressed EV/EBITDA multiple.
2. Administrative and other priority claims (professional fees, priority tax and
   employee claims, etc.) take an assumed percentage of EV off the top.
3. What is left (distributable value) pays claims in order of priority. Claims with
   the same priority share pro rata (pari passu). Lower priority numbers are paid first.
4. Anything left after all claims is residual equity value.

Claims are principal amounts only (no accrued interest, make-whole or fees).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Claim:
    name: str
    amount: float      # principal, $ millions
    priority: int      # 0 is paid first


@dataclass(frozen=True)
class Payout:
    name: str
    amount: float
    priority: int
    paid: float

    @property
    def recovery(self) -> float:
        """Fraction of the claim recovered (0 to 1). A zero-size claim recovers 0."""
        return self.paid / self.amount if self.amount > 0 else 0.0


@dataclass(frozen=True)
class WaterfallResult:
    distributable: float
    payouts: list[Payout]
    residual: float    # value left for equity after all claims

    @property
    def total_paid(self) -> float:
        return sum(p.paid for p in self.payouts)


def enterprise_value(ebitda: float, multiple: float) -> float:
    """EBITDA x multiple, floored at zero (a business is not worth less than nothing here)."""
    return max(ebitda * multiple, 0.0)


def distributable_value(ebitda: float, multiple: float, admin_pct: float) -> float:
    """EV less administrative / priority claims taken as a percentage of EV."""
    if not 0.0 <= admin_pct <= 1.0:
        raise ValueError("admin_pct must be between 0 and 1")
    return enterprise_value(ebitda, multiple) * (1.0 - admin_pct)


def run_waterfall(value: float, claims: list[Claim]) -> WaterfallResult:
    """Pay `value` to `claims` by priority (lowest number first), pro rata within a priority."""
    if any(c.amount < 0 for c in claims):
        raise ValueError("claim amounts must be non-negative")
    remaining = max(value, 0.0)
    paid: dict[int, float] = {}
    for prio in sorted({c.priority for c in claims}):
        group = [(i, c) for i, c in enumerate(claims) if c.priority == prio]
        owed = sum(c.amount for _, c in group)
        pay = min(remaining, owed)
        for i, c in group:
            paid[i] = pay * (c.amount / owed) if owed > 0 else 0.0
        remaining -= pay
    payouts = [Payout(c.name, c.amount, c.priority, paid[i]) for i, c in enumerate(claims)]
    return WaterfallResult(distributable=max(value, 0.0), payouts=payouts, residual=remaining)
