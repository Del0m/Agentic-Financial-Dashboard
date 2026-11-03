"""Load the reviewed guidance half, kb/graph.json, into the shared graph (tpc-38). Safe to run on every rebuild."""
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KGEdge, KGNode

from .store import assert_edge, upsert_node
from .types import KGError

logger = logging.getLogger(__name__)


@dataclass
class Loaded:
    edges: int = 0
    skipped: list[str] = field(default_factory=list)


def _date(value):
    return date.fromisoformat(value) if value else None


# the caller owns the transaction, like every graph write. oldest first, because the store refuses a backdated insert
def load_guidance(session: Session, path: Path) -> Loaded:
    data = json.loads(path.read_text(encoding="utf-8"))
    nodes = {}
    for c in data["concepts"]:
        nodes[("concept", c["key"])] = upsert_node(session, user_id=None, kind="concept", key=c["key"], label=c["label"])
    for s in data["sources"]:
        nodes[("source", s["slug"])] = upsert_node(session, user_id=None, kind="source", key=s["slug"], label=s["title"],
                                                   props={"url": s["url"]})
    out = Loaded()
    for e in sorted(data["edges"], key=lambda e: (e["valid_from"], e["src"], e["rel"], e["dst"])):
        kind = "source" if e["rel"] == "MENTIONED_IN" else "concept"
        src, dst = nodes[("concept", e["src"])], nodes[(kind, e["dst"])]
        start, end = _date(e["valid_from"]), _date(e.get("valid_to"))
        current = session.scalars(select(KGEdge).where(
            KGEdge.user_id.is_(None), KGEdge.src_id == src.id, KGEdge.rel == e["rel"], KGEdge.dst_id == dst.id,
            KGEdge.valid_from == start, KGEdge.expired_at.is_(None))).first()
        args = dict(user_id=None, src_id=src.id, rel=e["rel"], dst_id=dst.id, value=e["value"], valid_from=start,
                    valid_to=end, source="kb_extract", evidence=e.get("evidence"))
        try:
            if current is not None:
                # a reviewed fact that changed is corrected, so what the graph believed before stays in its history
                assert_edge(session, **args, correct=True, target_edge_id=current.id)
            else:
                assert_edge(session, **args)
            out.edges += 1
        except KGError as exc:
            logger.warning("guidance edge skipped: %s", type(exc).__name__)
            out.skipped.append(f"{e['src']} {e['rel']} {e['dst']} {e['valid_from']}")
    return out
