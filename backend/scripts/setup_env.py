"""Create the repo-root .env and give it a FERNET_KEY, so the app starts on a fresh clone.

Safe to run again: an existing .env is kept, and a FERNET_KEY already in it is never replaced, because the Plaid
tokens it encrypted couldn't be read any more. Plaid's own keys can't be generated here; they come from a Plaid
account and are optional for the demo.

`docker compose up` runs this for you. To run it by hand, from any directory:
    python backend/scripts/setup_env.py
"""
import shutil
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key

REPO = Path(__file__).resolve().parent.parent.parent


def ensure_env(repo: Path = REPO) -> None:
    env = repo / ".env"
    if not env.exists():
        shutil.copy(repo / ".env.example", env)
        print("created .env from .env.example")
    if dotenv_values(env).get("FERNET_KEY"):
        print("FERNET_KEY already set; left as it is")
        return
    set_key(env, "FERNET_KEY", Fernet.generate_key().decode(), quote_mode="never")
    print("generated a FERNET_KEY in .env")



if __name__ == "__main__":
    sys.exit(ensure_env())
