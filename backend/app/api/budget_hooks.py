import sqlite3
from functools import lru_cache

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from .. import config
from ..agents.llm import get_chat_model
from ..db.session import SessionLocal

# what the budget and alerts routes run against. the routes read these as budget_hooks.<name> at call time,
# so tests swap the db, the model and the checkpointer in one place

session_factory = SessionLocal
make_model = get_chat_model


@lru_cache(maxsize=1)
def _sqlite_saver() -> SqliteSaver:
    config.BUDGET_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.BUDGET_CHECKPOINT_PATH), check_same_thread=False)
    # a goal run that raises writes a checkpoint with debt names still in it (goal_graph). deleting it only frees the
    # rows, and sqlite leaves freed pages' bytes in the file unless this is on. the wal can hold them until it
    # checkpoints
    conn.execute("PRAGMA secure_delete=ON")
    return SqliteSaver(conn)


def get_checkpointer() -> BaseCheckpointSaver:
    return _sqlite_saver()
