"""Seed the demo user on a simulated date, with its first budget open, so the app doesn't start empty.

The planted data is dated, so the user lives on a simulated date instead of today's: the demo looks the same
whenever it's run. Advancing to the 1st opens the budget and flags nothing, so this works before Ollama is up.

Safe to run again: it never touches a user that's already set up, so advanced days survive a restart.
`docker compose up` runs this for you. To run it by hand, from backend/:
    python scripts/setup_demo.py
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# make `app`, `scripts`, `tests` and `eval` importable however the script is launched
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from cryptography.fernet import Fernet

# app.security fails at import without a key; seeding and simulating never decrypt anything
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from app.budget_service.daily import sim_prefs
from app.db.models import BudgetPeriod, User
from scripts import seed_budget_user, simulate

DEMO_PROFILE = "overspend-recurring"
DEMO_USER = f"budget-{DEMO_PROFILE}"


def main(session_factory=None, model=None) -> int:
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    with session_factory() as s:
        exists = s.get(User, DEMO_USER) is not None
    if not exists:
        code = seed_budget_user.main(["--profile", DEMO_PROFILE, "--start", "default"], session_factory=session_factory)
        if code != 0:
            return code
    with session_factory() as s:
        simulated = sim_prefs(s, DEMO_USER)
        has_budget = s.query(BudgetPeriod).filter_by(user_id=DEMO_USER).first() is not None
    if not simulated or has_budget:
        print(f"demo user {DEMO_USER} already exists; left as it is")
        return 0
    return simulate.main(["--user", DEMO_USER, "--days", "1"], session_factory=session_factory, model=model)


if __name__ == "__main__":
    sys.exit(main())
