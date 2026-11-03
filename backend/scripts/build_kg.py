"""Build one user's financial graph from their history, including seeded and simulated users.

The daily run does this by itself the first time it sees a user, so this is for looking at a graph without a run, or
after a migration. Facts already recorded are left alone. Run from backend/:
    ../venv/bin/python scripts/build_kg.py --user budget-overspend-recurring
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from app.api import budget_hooks
from app.db.models import KGEdge, User
from app.kg.project import backfill, current_day


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--user", required=True, help="user id")
    args = p.parse_args(argv)
    with budget_hooks.session_factory() as s:
        if s.get(User, args.user) is None:
            print(f"No user {args.user}.")
            return 2
        result = backfill(s, args.user, current_day(s, args.user))
        s.commit()
        edges = s.query(KGEdge).filter(KGEdge.user_id == args.user, KGEdge.expired_at.is_(None)).count()
    print(f"{args.user}: {result.asserted} new facts, {result.corrected} corrected, {len(result.skipped)} skipped, "
          f"{edges} current edges.")
    for skipped in result.skipped:
        print(f"  skipped {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
