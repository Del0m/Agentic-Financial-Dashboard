from datetime import date

import pytest
from sqlalchemy import select

from app.agents.budget_graph import run_budget
from app.budget_engine.txns import Txn
from app.budget_service.alerts import decide_alert
from app.budget_service.simulate import advance_days
from app.db.models import AdviceLog, Alert, BudgetLine, CategoryRule, KGEdge, KGNode, RecurringStream
from app.kg import project, query
from app.kg.project import spends_at
from eval.budget_users import profile_by_id, seed_profile
from eval.fixtures import temp_db
from tests.factories import add_account, add_txn, add_user
from tests.fakes import SchemaReplyModel


@pytest.fixture(autouse=True)
def audit_in_tmp(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


def beliefs(session, user_id):
    nodes = {n.id: (n.kind, n.key) for n in session.scalars(select(KGNode))}
    edges = session.scalars(select(KGEdge).where(KGEdge.user_id == user_id, KGEdge.expired_at.is_(None))).all()
    return sorted((nodes[e.src_id], e.rel, nodes[e.dst_id], str(e.value_json), e.valid_from, e.valid_to) for e in edges)


def edges_of(session, user_id, rel):
    return session.execute(select(KGEdge, KGNode).join(KGNode, KGNode.id == KGEdge.dst_id).where(
        KGEdge.user_id == user_id, KGEdge.rel == rel, KGEdge.expired_at.is_(None)).order_by(KGEdge.valid_from)).all()


def simulated_month():
    profile = profile_by_id("overspend-recurring")
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile, until=date(2026, 9, 30))
        advance_days(Session, uid, 20, model=SchemaReplyModel(), checkpointer=None)
        with Session() as s:
            return beliefs(s, uid)


# the ticket's done-when
def test_simulating_a_month_twice_gives_the_same_graph():
    first = simulated_month()
    assert first and first == simulated_month()
    assert {e[1] for e in first} >= {"SPENDS_AT", "BUDGETS", "HAS_ACCOUNT", "SUBSCRIBES", "ABOUT"}




def test_a_rule_made_mid_month_opens_its_edge_from_that_date(db):
    with temp_db() as Session, Session() as s:
        uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 9, 30))
        s.add(CategoryRule(user_id=uid, merchant_name="fitclub gym", category="ENTERTAINMENT"))
        s.flush()
        project.project_rules(s, uid, date(2026, 9, 30))
        [(edge, category)] = edges_of(s, uid, "CATEGORIZED_AS")
        assert (category.key, edge.valid_from, edge.valid_to, edge.source) == ("ENTERTAINMENT", date(2026, 9, 30), None, "user")


def test_accepting_a_proposal_keeps_the_pending_belief_in_history():
    profile = profile_by_id("overspend-recurring")
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile, until=date(2026, 10, 3))
        model = SchemaReplyModel(cause="recurring", first_action="adjust_next_budget")
        advance_days(Session, uid, 2, model=model, checkpointer=None)
        with Session() as s:
            alert = s.scalars(select(Alert).where(Alert.user_id == uid, Alert.action == "adjust_next_budget")).first()
            assert alert is not None
            advice = s.scalars(select(AdviceLog).where(AdviceLog.alert_id == alert.id)).one()
            node = s.scalars(select(KGNode).where(KGNode.key == f"advice-{advice.id}")).one()
            before = query.history(s, user_id=uid, node_id=node.id, rel="ABOUT")
            assert [e.value_json["outcome"] for e in before] == [None]
        decide_alert(Session, uid, alert.id, "accepted")
        with Session() as s:
            assert [e.value_json["outcome"] for e in query.history(s, user_id=uid, node_id=node.id, rel="ABOUT")] == ["accepted"]
            audit = query.history(s, user_id=uid, node_id=node.id, rel="ABOUT", audit=True)
            assert sorted(str(e.value_json["outcome"]) for e in audit) == ["None", "accepted"]
            assert len({(e.valid_from, e.valid_to) for e in audit}) == 1
            assert sum(e.expired_at is None for e in audit) == 1


# pure projection
def t(y, m, d, cents, category="FOOD_AND_DRINK", merchant="Cafe"):
    return Txn(date(y, m, d), cents, category, merchant=merchant)


def test_spends_at_nets_refunds_and_bounds_the_month():
    [a] = spends_at("u", [t(2026, 8, 1, 1000), t(2026, 8, 9, -300), t(2026, 7, 31, 500), t(2026, 9, 1, 500)], date(2026, 8, 15))
    assert a.value == {"cents": 700, "count": 2, "category": "FOOD_AND_DRINK"}
    assert (a.valid_from, a.valid_to) == (date(2026, 8, 1), date(2026, 9, 1))


def test_spends_at_skips_income_and_no_merchant():
    txns = [t(2026, 8, 1, -5000, "INCOME", "Employer"), t(2026, 8, 2, 900, merchant=None)]
    assert spends_at("u", txns, date(2026, 8, 1)) == []


def test_spends_at_takes_the_category_with_the_most_spending():
    [a] = spends_at("u", [t(2026, 8, 1, 100, "TRAVEL"), t(2026, 8, 2, 900, "FOOD_AND_DRINK")], date(2026, 8, 1))
    assert a.value["category"] == "FOOD_AND_DRINK"


# writing
@pytest.fixture
def account(db):
    with db() as s:
        item = add_user(s, "u1", "u1@x")
        acct = add_account(s, item, "Checking", subtype="checking")
        s.commit()
        yield s, acct


def test_a_changed_month_corrects_the_edge_and_keeps_the_old_belief(account):
    s, acct = account
    add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 8, 3))
    project.project_month(s, "u1", date(2026, 8, 1))
    add_txn(s, acct, 500, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 8, 9))
    project.project_month(s, "u1", date(2026, 8, 1))
    [(edge, _)] = edges_of(s, "u1", "SPENDS_AT")
    assert edge.value_json["cents"] == 1500
    assert s.query(KGEdge).filter_by(rel="SPENDS_AT").count() == 2


def test_running_twice_adds_nothing(account):
    s, acct = account
    add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 8, 3))
    project.backfill(s, "u1", date(2026, 8, 20))
    count = s.query(KGEdge).count()
    result = project.project_run(s, "u1", date(2026, 8, 20))
    assert s.query(KGEdge).count() == count and result.corrected == 0 and result.skipped == []


def test_recategorizing_a_past_month_corrects_it(account):
    s, acct = account
    row = add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 7, 3))
    project.project_month(s, "u1", date(2026, 7, 1))
    s.flush()
    from app.db.models import Transaction
    s.scalars(select(Transaction)).one().user_category = "ENTERTAINMENT"
    s.flush()
    project.project_month(s, "u1", date(2026, 7, 1))
    [(edge, _)] = edges_of(s, "u1", "SPENDS_AT")
    assert edge.value_json["category"] == "ENTERTAINMENT" and edge.valid_from == date(2026, 7, 1)


def test_a_merchant_with_nothing_left_goes_to_zero(account):
    s, acct = account
    add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 7, 3))
    project.project_month(s, "u1", date(2026, 7, 1))
    from app.db.models import Transaction
    s.scalars(select(Transaction)).one().removed = True
    s.flush()
    project.project_month(s, "u1", date(2026, 7, 1))
    [(edge, _)] = edges_of(s, "u1", "SPENDS_AT")
    assert edge.value_json["cents"] == 0 and edge.value_json["count"] == 0


def test_a_backdated_month_is_skipped_and_reported(account):
    s, acct = account
    add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 8, 3))
    project.project_month(s, "u1", date(2026, 8, 1))
    add_txn(s, acct, 500, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 6, 3))
    result = project.project_month(s, "u1", date(2026, 6, 1))
    assert result.skipped == ["SPENDS_AT:cafe:2026-06-01"]


def test_replacing_and_deleting_a_rule(account):
    s, _ = account
    rule = CategoryRule(user_id="u1", merchant_name="cafe", category="ENTERTAINMENT")
    s.add(rule)
    s.flush()
    created = rule.created_at.date()
    project.project_rules(s, "u1", created)
    rule.category = "TRAVEL"
    s.flush()
    project.project_rules(s, "u1", created)       # the same day corrects the edge
    [(edge, category)] = edges_of(s, "u1", "CATEGORIZED_AS")
    assert category.key == "TRAVEL" and edge.valid_from == created
    s.delete(rule)
    s.flush()
    project.project_rules(s, "u1", created)
    [(edge, _)] = edges_of(s, "u1", "CATEGORIZED_AS")
    assert edge.valid_to is not None and edge.valid_to > edge.valid_from


def _stream(**kw):
    fields = dict(user_id="u1", direction="out", merchant_key="gym", merchant_name="Gym", category="PERSONAL_CARE",
                  frequency="monthly", typical_cents=3500, last_cents=3500, amount_varies=False,
                  first_date=date(2026, 3, 1), first_seen_date=date(2026, 3, 1), last_date=date(2026, 3, 1), next_date=date(2026, 4, 1), gap_days=30,
                  status="active")
    return RecurringStream(**{**fields, **kw})


def test_a_price_change_closes_the_stream_edge_and_opens_a_new_one(account):
    s, _ = account
    stream = _stream()
    s.add(stream)
    s.flush()
    project.project_streams(s, "u1")
    stream.typical_cents, stream.last_date = 4300, date(2026, 8, 1)
    s.flush()
    project.project_streams(s, "u1")
    rows = [e for e, n in edges_of(s, "u1", "SUBSCRIBES") if n.kind == "stream"]
    assert [(e.value_json["typical_cents"], e.valid_from, e.valid_to) for e in rows] == [
        (3500, date(2026, 3, 1), date(2026, 8, 1)), (4300, date(2026, 8, 1), None)]


def test_a_stale_stream_ends_its_edge(account):
    s, _ = account
    stream = _stream()
    s.add(stream)
    s.flush()
    project.project_streams(s, "u1")
    stream.status, stream.last_date = "stale", date(2026, 6, 1)
    s.flush()
    project.project_streams(s, "u1")
    rows = [e for e, n in edges_of(s, "u1", "SUBSCRIBES") if n.kind == "stream"]
    assert [e.valid_to for e in rows] == [date(2026, 6, 2)]


def test_advice_with_no_category_has_no_edge(account):
    s, _ = account
    s.add(AdviceLog(user_id="u1", category=None, advice_text="Low balance."))
    s.flush()
    assert project.project_advice(s, "u1").asserted == 0


def test_a_bridge_points_at_a_shared_concept(account):
    s, _ = account
    project.apply(s, "u1", project.category_bridges(["LOAN_PAYMENTS", "TRAVEL"]))
    [(edge, concept)] = edges_of(s, "u1", "RELATES_TO")
    assert concept.key == "debt-avalanche" and concept.user_id is None


def test_a_failed_projection_rolls_back_only_itself(account):
    s, acct = account
    add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, 8, 3))
    s.flush()

    def boom():
        project.project_month(s, "u1", date(2026, 8, 1))
        raise RuntimeError("private detail")
    assert project.safely(s, "test", boom) is False
    assert s.query(KGEdge).count() == 0
    from app.db.models import Transaction
    assert s.query(Transaction).count() == 1


def test_a_graph_failure_doesnt_stop_the_budget_run(monkeypatch, tmp_path):
    def boom(*a):
        raise RuntimeError("private detail")
    monkeypatch.setattr(project, "project_run", boom)
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 10, 3))
        state = run_budget(Session, uid, date(2026, 10, 4), model=SchemaReplyModel(), audit_path=tmp_path / "a.jsonl")
    assert state["status"] != "error" and state.get("graph_error") is True


def test_close_projects_the_final_month_and_the_new_limits():
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 9, 29))
        advance_days(Session, uid, 3, model=SchemaReplyModel(), checkpointer=None)
        with Session() as s:
            rows = {e.valid_from for e, _ in edges_of(s, uid, "BUDGETS")}
            assert {date(2026, 9, 1), date(2026, 10, 1)} <= rows
            sept = [e for e, n in edges_of(s, uid, "SPENDS_AT") if e.valid_from == date(2026, 9, 1)]
            assert sept and all(e.valid_to == date(2026, 10, 1) for e in sept)


def test_backfill_script_builds_a_seeded_users_history():
    with temp_db() as Session, Session() as s:
        uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 9, 30))
        result = project.backfill(s, uid, date(2026, 9, 30))
        assert result.skipped == []
        months = {e.valid_from for e, _ in edges_of(s, uid, "SPENDS_AT")}
        assert months == {date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)}


def test_build_kg_script(monkeypatch, capsys):
    import importlib.util
    from pathlib import Path
    from app.api import budget_hooks
    spec = importlib.util.spec_from_file_location("build_kg", Path(__file__).parents[1] / "scripts" / "build_kg.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    with temp_db() as Session:
        monkeypatch.setattr(budget_hooks, "session_factory", Session)
        with Session() as s:
            uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 9, 30))
        assert script.main(["--user", uid]) == 0
        assert "0 skipped" in capsys.readouterr().out
        assert script.main(["--user", "nobody"]) == 2


def test_merchant_variants_sum_before_projection(account):
    s, acct = account
    for name, amount in [(" Cafe ", 1000), ("CAFE", 700), ("cafe", -200)]:
        add_txn(s, acct, amount, "FOOD_AND_DRINK", merchant=name, day=date(2026, 8, 3))
    project.project_month(s, "u1", date(2026, 8, 1))
    [(edge, node)] = edges_of(s, "u1", "SPENDS_AT")
    assert node.key == "cafe" and node.label == "Cafe"
    assert edge.value_json == {"cents": 1500, "count": 3, "category": "FOOD_AND_DRINK"}
    project.project_month(s, "u1", date(2026, 8, 1))
    assert s.query(KGEdge).filter_by(rel="SPENDS_AT").count() == 1


def test_real_users_rule_edit_keeps_previous_interval(account):
    from datetime import datetime, timezone
    s, _ = account
    rule = CategoryRule(user_id="u1", merchant_name="cafe", category="FOOD_AND_DRINK",
                        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    s.add(rule)
    s.flush()
    project.project_rules(s, "u1", date(2026, 8, 1))
    rule.category = "TRAVEL"
    s.flush()
    project.project_rules(s, "u1", date(2026, 9, 15))
    # A second edit on the same day corrects September, not August.
    rule.category = "ENTERTAINMENT"
    s.flush()
    project.project_rules(s, "u1", date(2026, 9, 15))
    assert [(n.key, e.valid_from, e.valid_to) for e, n in edges_of(s, "u1", "CATEGORIZED_AS")] == [
        ("FOOD_AND_DRINK", date(2026, 8, 1), date(2026, 9, 15)),
        ("ENTERTAINMENT", date(2026, 9, 15), None)]
    assert rule.created_at.date() == date(2026, 8, 1)
    count = s.query(KGEdge).count()
    project.project_rules(s, "u1", date(2026, 10, 1))
    assert s.query(KGEdge).count() == count


def test_overdue_close_then_daily_backfills_older_spending_and_budgets(tmp_path):
    from app.budget_service.close import close_due
    from app.budget_service.daily import run_daily_locked
    from app.budget_service.initialize import initialize_budget
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile_by_id("overspend-recurring"), until=date(2026, 9, 30))
            initialize_budget(s, uid, date(2026, 9, 30))
        close_due(Session, uid, date(2026, 10, 2))
        with Session() as s:
            assert project.has_graph(s, uid) and not project.backfill_complete(s, uid)
        state = run_daily_locked(Session, uid, date(2026, 10, 2), model=SchemaReplyModel(), checkpointer=None)
        assert state["status"] != "error" and not state.get("graph_error")
        with Session() as s:
            assert project.backfill_complete(s, uid)
            assert {date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1)} <= {
                e.valid_from for e, _ in edges_of(s, uid, "SPENDS_AT")}
            assert {date(2026, 9, 1), date(2026, 10, 1)} <= {e.valid_from for e, _ in edges_of(s, uid, "BUDGETS")}
            count = s.query(KGEdge).count()
            assert project.project_run(s, uid, date(2026, 10, 2)).skipped == []
            assert s.query(KGEdge).count() == count


@pytest.mark.parametrize("failure", ["partial", "exception"])
def test_unsuccessful_backfill_is_retryable(account, monkeypatch, failure):
    s, acct = account
    for month in (7, 8, 9):
        add_txn(s, acct, 1000, "FOOD_AND_DRINK", merchant="Cafe", day=date(2026, month, 3))
    # A recent month already exists, as when close runs before the first daily run.
    project.project_month(s, "u1", date(2026, 9, 1))
    s.commit()
    original = project.project_month

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[2].month == 8:
            if failure == "exception":
                raise RuntimeError("interrupted")
            result.skipped.append("injected failure")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(project, "project_month", interrupted)
        ok = project.safely(s, "backfill", lambda: project.project_run(s, "u1", date(2026, 9, 20)))
        assert ok is (failure == "partial")
        s.commit()
    assert not project.backfill_complete(s, "u1")
    assert project.project_run(s, "u1", date(2026, 9, 20)).skipped == []
    s.commit()
    assert project.backfill_complete(s, "u1")
    assert {e.valid_from.month for e, _ in edges_of(s, "u1", "SPENDS_AT")} == {7, 8, 9}
    count = s.query(KGEdge).count()
    project.project_run(s, "u1", date(2026, 9, 20))
    assert s.query(KGEdge).count() == count
