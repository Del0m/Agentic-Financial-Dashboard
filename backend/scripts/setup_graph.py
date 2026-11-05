"""Fill the temporal graph, so chat can use it before the first daily run. Needs no model.

Loads the reviewed guidance from kb/graph.json, then builds the history of each user that doesn't have one yet. A problem
here is reported and never stops the app starting, because chat and the budget agent both work without the graph.

Safe to run again: guidance that hasn't changed stays as it is, and a finished history isn't rebuilt.
`docker compose up` runs this for you. To run it by hand, from backend/, after alembic upgrade head:
    python scripts/setup_graph.py
"""
import os
import sys
from pathlib import Path

# make `app` importable however the script is launched (it lives in backend/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.fernet import Fernet

# app.security fails at import without a key; building the graph never decrypts anything
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select

from app import config
from app.db.models import User
from app.kg import project
from app.kg.guidance import load_guidance


def load_shared(session_factory, path: Path) -> None:
    if not path.exists():
        print(f"no {path.name}, so the guidance graph wasn't loaded")
        return
    try:
        with session_factory() as s:
            loaded = load_guidance(s, path)
            s.commit()
        print(f"guidance graph: {loaded.edges} facts, {len(loaded.skipped)} skipped")
    except Exception as exc:
        print(f"guidance graph not loaded: {type(exc).__name__}: {exc}")


def build_users(session_factory) -> None:
    with session_factory() as s:
        users = s.scalars(select(User.id).order_by(User.id)).all()
    for user_id in users:
        try:
            with session_factory() as s:
                if project.backfill_complete(s, user_id):
                    continue
                result = project.backfill(s, user_id, project.current_day(s, user_id))
                s.commit()
            print(f"graph for {user_id}: {result.asserted} facts, {len(result.skipped)} skipped")
        except Exception as exc:
            print(f"graph for {user_id} not built: {type(exc).__name__}")


def main(session_factory=None, path: Path = None) -> int:
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    load_shared(session_factory, path or config.KB_DIR / "graph.json")
    build_users(session_factory)
    return 0


if __name__ == "__main__":
    sys.exit(main())
