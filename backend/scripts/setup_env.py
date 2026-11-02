"""Create the repo-root .env and give it a FERNET_KEY, so the app starts on a fresh clone.

Safe to run again: an existing .env is kept, and a FERNET_KEY already in it is never replaced, because the Plaid
tokens it encrypted couldn't be read any more. Plaid's own keys can't be generated here; they come from a Plaid
account and are optional for the demo.

`docker compose up` runs this for you. To run it by hand, from any directory:
    python backend/scripts/setup_env.py
"""
import re
import shutil
import sys
from pathlib import Path

from cryptography.fernet import Fernet

REPO = Path(__file__).resolve().parent.parent.parent
KEY_LINE = re.compile(r"^FERNET_KEY[ \t]*=[ \t]*(.*?)[ \t]*\r?$", re.MULTILINE)


def ensure_env(repo: Path = REPO) -> None:
    env = repo / ".env"
    if not env.exists():
        shutil.copy(repo / ".env.example", env)
        print("created .env from .env.example")
    text = env.read_text(encoding="utf-8")
    found = KEY_LINE.search(text)
    if found and found.group(1).strip("\"'"):
        print("FERNET_KEY already set; left as it is")
        return
    line = f"FERNET_KEY={Fernet.generate_key().decode()}"
    if found:
        text = text[:found.start()] + line + text[found.end():]
    else:
        text = (text if not text or text.endswith("\n") else text + "\n") + line + "\n"
    env.write_text(text, encoding="utf-8")
    print("generated a FERNET_KEY in .env")


if __name__ == "__main__":
    sys.exit(ensure_env())
