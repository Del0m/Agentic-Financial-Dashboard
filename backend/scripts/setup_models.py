"""Pull the Ollama models the app needs, then build the knowledge base if it's empty.

Separate from the app's startup because the chat model is a 9 GB download: the app can already be open while this
runs. Safe to run again: models already there and a knowledge base already built are skipped.

`docker compose up` runs this for you. To run it again:
    docker compose run --rm models
"""
import sys
import time
from pathlib import Path

import httpx
import ollama

# make `app` importable however the script is launched (it lives in backend/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.rag.fetch import fetch_all
from app.rag.store import KnowledgeBase, ingest

# an ollama container can take a moment to come up
WAIT_SECONDS = 30


def _with_tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


def _wait_for_models(client) -> set[str] | None:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            return {m.model for m in client.list().models}
        except (ConnectionError, httpx.HTTPError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(2)


def _pull(client, name: str) -> None:
    print(f"pulling {name}; the first download can take a while", flush=True)
    last = -10
    for part in client.pull(name, stream=True):
        if part.total and part.completed:
            percent = part.completed * 100 // part.total
            if percent >= last + 10:           # a line per 10%, not one per chunk
                last = percent
                print(f"  {name}: {percent}%", flush=True)
    print(f"{name} ready", flush=True)


def fetch_sources() -> None:
    with httpx.Client() as client:
        failed = fetch_all(config.KB_DIR, client)
    if failed:
        raise OSError(", ".join(failed))


def kb_count() -> int:
    return KnowledgeBase().count()


def ingest_kb() -> None:
    chunks, sources = ingest(config.KB_DIR, KnowledgeBase())
    print(f"knowledge base ready: {chunks} chunks from {sources} sources")


def main(client=None) -> int:
    client = client or ollama.Client(host=config.OLLAMA_BASE_URL)
    have = _wait_for_models(client)
    if have is None:
        print(f"Can't reach Ollama at {config.OLLAMA_BASE_URL}. The app still works, except chat and the budget "
              "agent's advice. Start Ollama, then run: docker compose run --rm models")
        return 1
    for name in (config.OLLAMA_MODEL, config.OLLAMA_EMBED_MODEL):
        if _with_tag(name) in have:
            print(f"{name} already there")
            continue
        try:
            _pull(client, name)
        except Exception as exc:
            print(f"couldn't pull {name}: {exc}. Fix it, then run: docker compose run --rm models")
            return 1
    if kb_count() > 0:
        print("knowledge base already built")
        return 0
    try:
        fetch_sources()
    except Exception as exc:
        print(f"building the knowledge base without a page that couldn't be fetched: {exc}")
    ingest_kb()
    print("all set: chat and the budget agent are ready", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
