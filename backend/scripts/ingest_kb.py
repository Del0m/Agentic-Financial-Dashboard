"""Chunk and embed kb/sources/ into the Chroma collection behind search_kb.

Rebuilds the collection from scratch every run. Needs Ollama running with
OLLAMA_EMBED_MODEL pulled (`ollama pull nomic-embed-text`).

Run from any directory:
    python backend/scripts/ingest_kb.py
"""
import sys
from pathlib import Path

# make `app` importable however the script is launched (it lives in backend/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from sqlalchemy.exc import OperationalError

from app.db.session import SessionLocal
from app.kg.guidance import load_guidance
from app.rag.store import KnowledgeBase, ingest


# the guidance half reloads with the text it came from. a database that isn't migrated yet doesn't stop the rebuild
def load_graph() -> None:
    path = config.KB_DIR / "graph.json"
    if not path.exists():
        print("no kb/graph.json, so the guidance graph wasn't loaded")
        return
    try:
        with SessionLocal() as session:
            loaded = load_guidance(session, path)
            session.commit()
        print(f"{loaded.edges} guidance facts loaded, {len(loaded.skipped)} skipped")
    except OperationalError as exc:
        print(f"guidance graph not loaded, run alembic upgrade head first: {type(exc).__name__}")


def main() -> None:
    chunks, sources = ingest(config.KB_DIR, KnowledgeBase())
    print(f"{chunks} chunks from {sources} sources")
    load_graph()


if __name__ == "__main__":
    main()
