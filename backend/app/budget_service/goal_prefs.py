from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.agents.tools import VALID_CATEGORIES
from app.budget_engine.goals import GOAL_KINDS
from app.db.models import User

# what the user wants from goals (tpc-23), kept in prefs_json["goals"]. code enforces skip_kinds and max_share;
# protected_categories and the note reach the model. goals are only funded from the surplus and never cut a line

MAX_NOTE_CHARS = 500
FIELDS = frozenset({"skip_kinds", "max_share", "protected_categories", "note"})


@dataclass(frozen=True)
class GoalPrefs:
    skip_kinds: tuple[str, ...] = ()
    max_share: float = 0.8         # the most of the surplus goals may use
    protected_categories: tuple[str, ...] = ()
    note: Optional[str] = None


def _prefs(raw: dict) -> GoalPrefs:
    return GoalPrefs(tuple(raw.get("skip_kinds", ())), raw.get("max_share", GoalPrefs.max_share),
                     tuple(raw.get("protected_categories", ())), raw.get("note"))


def user_goal_prefs(session: Session, user_id: str) -> GoalPrefs:
    user = session.get(User, user_id)
    return _prefs(((user.prefs_json or {}).get("goals") or {}) if user else {})


# validates and stages the change; the caller commits
def set_goal_prefs(session: Session, user_id: str, raw: dict) -> GoalPrefs:
    if not isinstance(raw, dict):
        raise ValueError("Goal preferences must be a JSON object.")
    unknown = set(raw) - FIELDS
    if unknown:
        raise ValueError(f"Unknown goal preference: {', '.join(sorted(unknown))}.")
    prefs = _prefs(raw)
    if any(k not in GOAL_KINDS for k in prefs.skip_kinds):
        raise ValueError(f"skip_kinds must be from: {', '.join(GOAL_KINDS)}.")
    share = prefs.max_share
    if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
        raise ValueError("max_share must be above 0 and at most 1.")
    if any(c not in VALID_CATEGORIES for c in prefs.protected_categories):
        raise ValueError("protected_categories must be budget categories.")
    if prefs.note is not None and (not isinstance(prefs.note, str) or len(prefs.note) > MAX_NOTE_CHARS):
        raise ValueError(f"note must be text of at most {MAX_NOTE_CHARS} characters.")
    user = session.get(User, user_id)
    user.prefs_json = {**(user.prefs_json or {}), "goals": dict(raw)}     # a new dict, so the json column sees it
    return prefs
