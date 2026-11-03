from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.budget_engine.recurring import INCOME, METERED_UTILITIES, RTxn, Stream, detect
from app.budget_engine.txns import Txn, spending
from app.budget_service.initialize import category_rules, history_start, resolve_category
from app.db.models import AdviceLog, AgentRun, Alert, RecurringStream, Transaction

# the detector's streams, stored and compared with the previous run (tpc-33). nothing here commits; the caller does

WINDOW_DAYS = 180
COPIED = ("merchant_name", "category", "detailed", "frequency", "typical_cents", "last_cents", "previous_cents",
          "amount_varies", "last_date", "next_date", "gap_days", "status")


@dataclass(frozen=True)
class StreamChange:
    kind: Literal["new_subscription", "price_increase", "income_change"]
    stream_id: int
    previous_cents: Optional[int]
    token: str                     # what makes this signal one of a kind: see alerts.signal_key


def load_rtxns(session: Session, user_id: str, today: date) -> list[RTxn]:
    rules = category_rules(session, user_id)
    rows = session.scalars(select(Transaction).where(
        Transaction.user_id == user_id, Transaction.removed.is_(False), Transaction.pending.is_(False),
        Transaction.date >= today - timedelta(days=WINDOW_DAYS), Transaction.date <= today))
    return [RTxn(r.date, r.amount_cents, r.merchant_name, resolve_category(r, rules)[0], r.pf_category_detailed)
            for r in rows]


def current_streams(session: Session, user_id: str, today: date) -> list[Stream]:
    return detect(load_rtxns(session, user_id, today), today, window_days=WINDOW_DAYS)


def stored_streams(session: Session, user_id: str) -> list[RecurringStream]:
    return list(session.scalars(select(RecurringStream).where(RecurringStream.user_id == user_id)
                                .order_by(RecurringStream.id)))


def muted_stream_ids(session: Session, user_id: str) -> set[int]:
    return set(session.scalars(select(Alert.stream_id).where(
        Alert.user_id == user_id, Alert.action == "suggest_cancel", Alert.status == "accepted",
        Alert.stream_id.is_not(None))))


def recurring_snapshot(session: Session, user_id: str) -> dict:
    rows = stored_streams(session, user_id)
    summaries = session.scalars(select(AgentRun.summary_json).where(
        AgentRun.user_id == user_id, AgentRun.graph == "budget").order_by(AgentRun.id.desc()))
    as_of = next((date.fromisoformat(s["as_of"]) for s in summaries if s and s.get("as_of")),
                 max((r.first_seen_date for r in rows), default=None))
    changes = {}
    if as_of is not None:
        for alert, summary in session.execute(select(Alert, AgentRun.summary_json).outerjoin(
                AdviceLog, AdviceLog.alert_id == Alert.id).outerjoin(AgentRun, AdviceLog.run_id == AgentRun.id).where(
                Alert.user_id == user_id, Alert.stream_id.in_([r.id for r in rows]),
                Alert.kind.in_(("new_subscription", "price_increase", "income_change"))).order_by(Alert.id)):
            day = date.fromisoformat(summary["as_of"]) if summary and summary.get("as_of") else alert.created_at.date()
            if as_of - timedelta(days=30) <= day <= as_of:
                changes[alert.stream_id] = "new" if alert.kind == "new_subscription" else alert.kind
    streams = [{"id": r.id, "direction": r.direction, "label": "Income" if r.direction == "in" else r.merchant_name,
                "frequency": r.frequency, "amount_kind": "varies" if r.amount_varies else "fixed",
                "typical_cents": r.typical_cents, "last_cents": r.last_cents, "last_date": r.last_date,
                "next_date": r.next_date, "status": r.status, "change": changes.get(r.id),
                "previous_cents": r.previous_cents, "first_seen": r.first_seen_date} for r in rows]
    streams.sort(key=lambda r: (2 if r["status"] == "stale" else 0 if r["change"] else 1, r["next_date"], r["id"]))
    return {"as_of": as_of, "streams": streams}


# income, transfers, savings and card payments aren't things a user subscribes to
def _is_spending(today: date, category: str, detailed: Optional[str]) -> bool:
    return bool(spending([Txn(today, 1, category, detailed)]))


# on a user's first run every stream is new to us, not to them: only one that began after the history did is news
def _is_new(s: Stream, today: date, start: Optional[date]) -> bool:
    return (s.direction == "out" and s.status == "active" and _is_spending(today, s.category, s.detailed)
            and start is not None and s.first_date - timedelta(days=s.gap_days) > start)


def _price_rose(s: Stream, today: date) -> bool:
    if s.previous_cents is None or s.direction != "out" or s.detailed in METERED_UTILITIES:
        return False
    return (_is_spending(today, s.category, s.detailed) and s.last_cents * 100 >= s.previous_cents * 105
            and s.last_cents - s.previous_cents >= 100)


def _is_pay(row: RecurringStream) -> bool:
    return row.direction == "in" and row.category == INCOME


# one transaction: updates matches, inserts new streams, stales the ones no longer found. never deletes, so a stream
# that returns is the same row, and silent
def refresh_streams(session: Session, user_id: str, today: date, run_id: Optional[int]) -> list[StreamChange]:
    rows = {(r.direction, r.merchant_key): r for r in stored_streams(session, user_id)}
    start = history_start(session, user_id, today)
    changes: list[StreamChange] = []
    found = set()
    for s in current_streams(session, user_id, today):
        found.add((s.direction, s.merchant_key))
        row = rows.get((s.direction, s.merchant_key))
        if row is None:
            row = RecurringStream(user_id=user_id, direction=s.direction, merchant_key=s.merchant_key,
                                  first_date=s.first_date, first_seen_run_id=run_id, first_seen_date=today,
                                  **{f: getattr(s, f) for f in COPIED})
            session.add(row)
            session.flush()
            if _is_new(s, today, start):
                changes.append(StreamChange("new_subscription", row.id, None, "first_seen"))
            continue
        was_active, advanced, typical_before = row.status == "active", s.last_date > row.last_date, row.typical_cents
        for f in COPIED:
            setattr(row, f, getattr(s, f))
        row.first_date = min(row.first_date, s.first_date)
        if not was_active:
            continue
        if s.status == "stale":
            if _is_pay(row):
                changes.append(StreamChange("income_change", row.id, row.last_cents, today.isoformat()))
        elif advanced and _price_rose(s, today):
            changes.append(StreamChange("price_increase", row.id, s.previous_cents, s.last_date.isoformat()))
        elif advanced and _is_pay(row) and s.last_cents * 10 <= typical_before * 9:
            changes.append(StreamChange("income_change", row.id, typical_before, s.last_date.isoformat()))
    for key, row in rows.items():
        if key not in found and row.status == "active":
            row.status = "stale"
            if _is_pay(row):
                changes.append(StreamChange("income_change", row.id, row.last_cents, today.isoformat()))
    session.flush()
    muted = muted_stream_ids(session, user_id)
    return [c for c in changes if c.stream_id not in muted]
