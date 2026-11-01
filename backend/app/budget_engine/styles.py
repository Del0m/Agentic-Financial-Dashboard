from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Mapping, Optional, Sequence

from app.budget_engine.pace import BudgetLine
from app.budget_engine.txns import FREE, SAVINGS, round_cents

# how a budget's lines, total and free money fit together (tpc-22). init and close both call apply_style once the
# per-line limits are known. free is never below $0: when the lines don't fit, the total rises and the user is
# told, but code never cuts a line the user didn't ask to cut (architecture 6.6)

STYLES = ("caps", "50_30_20")
DEFAULT_TARGETS = {"needs": 50, "wants": 30, "saving": 20}


@dataclass(frozen=True)
class StyleFact:
    kind: Literal["over_income", "group_over_target"]
    group: Optional[str]          # needs | wants for group_over_target
    lines_cents: int              # what the lines (or the group's lines) add up to
    income_cents: int
    target_pct: Optional[int]


@dataclass(frozen=True)
class StyleResult:
    limits: dict[str, int]        # every line's final limit by category, FREE included
    total_cents: int
    facts: tuple[StyleFact, ...]
    fallback: Optional[str]       # "no_income" when 50_30_20 had to run as caps


# lines excludes the free line; its limit is always computed here
def apply_style(style: str, lines: Sequence[BudgetLine], income_cents: Optional[int], fallback_total_cents: int,
                targets: Mapping[str, int]) -> StyleResult:
    if style == "50_30_20" and income_cents:
        return _split(lines, income_cents, targets)
    result = _caps(lines, income_cents, fallback_total_cents)
    if style == "50_30_20":
        return StyleResult(result.limits, result.total_cents, result.facts, "no_income")
    return result


# the total is income (or, with no income history, past spending); free is whatever the lines leave
def _caps(lines, income_cents, fallback_total_cents) -> StyleResult:
    base = income_cents or fallback_total_cents
    planned = sum(ln.limit_cents for ln in lines)
    total = max(base, planned)
    facts = (StyleFact("over_income", None, planned, income_cents, None),) \
        if income_cents and planned > income_cents else ()
    return StyleResult({**{ln.category: ln.limit_cents for ln in lines}, FREE: total - planned}, total, facts, None)


# income split by group: saving is its target, free is the room left in wants, and a group over its target is
# reported rather than trimmed
def _split(lines, income_cents, targets) -> StyleResult:
    target = {g: round_cents(Decimal(income_cents) * targets[g] / 100) for g in ("needs", "wants", "saving")}
    limits = {ln.category: ln.limit_cents for ln in lines}
    limits[SAVINGS] = target["saving"]
    by_group = {g: sum(ln.limit_cents for ln in lines if ln.group == g) for g in ("needs", "wants")}
    limits[FREE] = max(target["wants"] - by_group["wants"], 0)
    facts = tuple(StyleFact("group_over_target", g, by_group[g], income_cents, targets[g])
                  for g in ("needs", "wants") if by_group[g] > target[g])
    return StyleResult(limits, max(income_cents, sum(limits.values())), facts, None)
