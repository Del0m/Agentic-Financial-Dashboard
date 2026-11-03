from datetime import date

from app.budget_service.alerts import decide_alert
from app.budget_service.close import close_due
from app.budget_service.recurring import muted_stream_ids, refresh_streams, stored_streams
from app.db.models import Account, Alert
from tests.factories import add_txn
from tests.test_close import add_alert, lines_of, period_at, setup


def cancel(db, *, status="accepted", limit=10000, category="ENTERTAINMENT"):
    pid, _ = setup(db, lines=(("ENTERTAINMENT", "flexible", limit),), fun=limit, food=0, rent=0, travel=0, saved=0)
    with db() as s:
        acct = s.query(Account).one()
        for month in (7, 8, 9, 10):
            add_txn(s, acct, 1599, "ENTERTAINMENT", merchant="Streamly", day=date(2026, month, 5))
        refresh_streams(s, "u1", date(2026, 10, 20), None)
        stream = next(r for r in stored_streams(s, "u1") if r.merchant_name == "Streamly")
        stream.category = category
        alert = Alert(user_id="u1", period_id=pid, stream_id=stream.id, kind="new_subscription", category=category,
                      severity="medium", action="suggest_cancel", message="Consider cancelling.", status=status)
        s.add(alert)
        s.commit()
        return pid, stream.id, alert.id


def test_accepting_suggest_cancel_lowers_the_line_at_close(db):
    cancel(db)
    [summary] = close_due(db, "u1", date(2026, 11, 1))
    assert lines_of(period_at(db, date(2026, 11, 1)))["ENTERTAINMENT"].limit_cents == 8500
    assert summary["cancel_outcomes"][0]["outcome"] == "applied"


def test_an_accepted_adjust_and_an_accepted_cancel_both_apply_in_order(db):
    pid, _, _ = cancel(db)
    add_alert(db, pid, "ENTERTAINMENT", 12000, "accepted")
    close_due(db, "u1", date(2026, 11, 1))
    assert lines_of(period_at(db, date(2026, 11, 1)))["ENTERTAINMENT"].limit_cents == 10500


def test_a_category_with_no_line_is_unchanged_and_the_outcome_is_recorded(db):
    cancel(db, category="PERSONAL_CARE")
    [summary] = close_due(db, "u1", date(2026, 11, 1))
    assert summary["cancel_outcomes"][0]["outcome"] == "no_line"
    assert "PERSONAL_CARE" not in lines_of(period_at(db, date(2026, 11, 1)))


def test_a_limit_the_cancel_would_take_to_zero_is_left_alone(db):
    cancel(db, limit=1000)
    [summary] = close_due(db, "u1", date(2026, 11, 1))
    assert summary["cancel_outcomes"][0]["outcome"] == "nonpositive"
    assert lines_of(period_at(db, date(2026, 11, 1)))["ENTERTAINMENT"].limit_cents > 0


def test_a_pending_suggest_cancel_survives_month_end(db):
    _, _, aid = cancel(db, status="pending_user")
    close_due(db, "u1", date(2026, 11, 1))
    with db() as s:
        assert s.get(Alert, aid).status == "pending_user"
    decide_alert(db, "u1", aid, "accepted")
    [summary] = close_due(db, "u1", date(2026, 12, 1))
    assert summary["cancel_outcomes"][0]["alert_id"] == aid


def test_a_muted_stream_raises_no_more_signals(db):
    _, sid, _ = cancel(db)
    with db() as s:
        add_txn(s, s.query(Account).one(), 1799, "ENTERTAINMENT", merchant="Streamly", day=date(2026, 11, 5))
        assert sid in muted_stream_ids(s, "u1")
        assert all(c.stream_id != sid for c in refresh_streams(s, "u1", date(2026, 11, 5), None))
        assert next(r for r in stored_streams(s, "u1") if r.id == sid).last_cents == 1799


def test_decline_changes_nothing(db):
    cancel(db, status="dismissed")
    [summary] = close_due(db, "u1", date(2026, 11, 1))
    assert summary["cancel_outcomes"] == []
    with db() as s:
        assert muted_stream_ids(s, "u1") == set()


def test_a_cancellation_is_applied_only_once_across_months(db):
    cancel(db)
    close_due(db, "u1", date(2026, 11, 1))
    [summary] = close_due(db, "u1", date(2026, 12, 1))
    assert summary["cancel_outcomes"] == []
