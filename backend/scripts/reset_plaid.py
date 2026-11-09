"""Reset the local Plaid Sandbox demo user, leaving it ready for a new Link session.

Stop the backend first. Preview: python scripts/reset_plaid.py
Apply: python scripts/reset_plaid.py --yes
Only dev@localhost is reset. A SQLite backup is required and created before deletion.
Old Sandbox Items are not revoked at Plaid; a new Link session starts fresh history.
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.plaid_service.demo_reset import EMAIL, backup_database, checked_user, reset_user


def main(argv=None, session_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="apply reset after backing up the database")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    try:
        with session_factory() as s:
            user = checked_user(s)
            print(f"Target: {EMAIL}; existing user: {'yes' if user else 'no'}")
            print("Clears this user's local bank links, accounts, transactions, budgets, alerts, goals and memory.")
            print("Stop the backend first. The seeded Demo user is preserved. No Plaid Items are revoked remotely.")
            if not args.yes:
                print("Preview only. Pass --yes to back up and reset.")
                return 0
            backup = backup_database(s.get_bind())
            print(f"Database backup: {backup}", flush=True)
            reset_user(s)
            s.commit()
    except Exception as exc:
        # Never echo tokens or raw database errors.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"Reset failed: {message}")
        return 1
    print("Reset complete. Restart the backend, reload Dashboard, select Plaid bank and Connect a bank.")
    print("Or reconnect automatically: docker compose exec backend python scripts/sandbox_link.py --dynamic")
    print("Plaid uses today's real date. Run a budget check after the fresh bank history has loaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
