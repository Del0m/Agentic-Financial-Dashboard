from sqlalchemy.orm import Session

from app.db.models import User

DEFAULT_BUFFER_CENTS = 10000


def user_buffer(session: Session, user_id: str) -> int:
    user = session.get(User, user_id)
    prefs = user.prefs_json or {} if user else {}
    value = (prefs.get("forecast") or {}).get("low_balance_buffer_cents", prefs.get("low_balance_buffer_cents", DEFAULT_BUFFER_CENTS))
    return value if type(value) is int and 0 <= value <= 1000000 else DEFAULT_BUFFER_CENTS


def set_user_buffer(session: Session, user_id: str, cents: int) -> None:
    if type(cents) is not int or not 0 <= cents <= 1000000:
        raise ValueError("The buffer is $0 to $10,000.")
    user = session.get(User, user_id)
    prefs = user.prefs_json or {}
    user.prefs_json = {**prefs, "forecast": {**(prefs.get("forecast") or {}), "low_balance_buffer_cents": cents}}
