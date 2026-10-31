from typing import Optional

from sqlalchemy.orm import Session

from app.budget_engine.styles import DEFAULT_TARGETS, STYLES
from app.db.models import User

# the user's budget style (tpc-22), kept in prefs_json["budget"]. it takes effect at the next init or close,
# never mid-month: a month is judged against the budget it started with


def user_style(session: Session, user_id: str) -> tuple[str, dict[str, int]]:
    user = session.get(User, user_id)
    budget = ((user.prefs_json or {}).get("budget") or {}) if user else {}
    return budget.get("style", "caps"), dict(budget.get("targets") or DEFAULT_TARGETS)


# validates and stages the change; the caller commits
def set_user_style(session: Session, user_id: str, style: str, targets: Optional[dict[str, int]] = None) -> None:
    if style not in STYLES:
        raise ValueError(f"Unknown budget style: {style}.")
    targets = dict(targets or DEFAULT_TARGETS)
    if set(targets) != set(DEFAULT_TARGETS) or any(not isinstance(v, int) or v < 0 for v in targets.values()) \
            or sum(targets.values()) != 100:
        raise ValueError("Targets must add up to 100.")
    user = session.get(User, user_id)
    user.prefs_json = {**(user.prefs_json or {}), "budget": {"style": style, "targets": targets}}
