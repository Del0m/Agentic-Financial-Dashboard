import json
from datetime import date

from langchain_core.messages import AIMessage

from app.agents.budget_graph import run_budget
from app.budget_service.initialize import initialize_budget
from app.db.models import AdviceLog, AgentRun, Alert, BudgetPeriod, CategoryRule, Goal, Liability, Transaction, User
from eval.budget_users import PLANTED, transactions
from scripts.seed_budget_user import main
from tests.fakes import ScriptedChatModel

PID = "overspend-one-time"
UID = f"budget-{PID}"
N = len(transactions(next(p for p in PLANTED if p.id == PID)))


def count(db, cls, **f):
    with db() as s:
        return s.query(cls).filter_by(**f).count()


def test_seed_writes_rows(db, capsys):
    assert main(["--profile", PID], session_factory=db) == 0
    assert count(db, Transaction, user_id=UID) == N
    out = capsys.readouterr().out
    assert UID in out and "--date 2026-10-20" in out


def test_seed_refuses_existing(db, capsys):
    main(["--profile", PID], session_factory=db)
    assert main(["--profile", PID], session_factory=db) == 1
    assert count(db, Transaction, user_id=UID) == N
    assert "--replace" in capsys.readouterr().out


def test_replace_deletes_and_reseeds(db, tmp_path):
    main(["--profile", PID], session_factory=db)
    replies = [AIMessage(content=json.dumps({"cause": "one_time", "evidence": "One purchase."})),
               AIMessage(content=json.dumps({"action": "alert", "message": "Over."}))]
    run_budget(db, UID, date(2026, 10, 20), model=ScriptedChatModel(replies=replies),
               audit_path=tmp_path / "a.jsonl")
    assert count(db, Alert, user_id=UID) == 1
    assert main(["--profile", PID, "--replace"], session_factory=db) == 0
    assert count(db, Transaction, user_id=UID) == N and count(db, User, id=UID) == 1
    for cls in (BudgetPeriod, Alert, AdviceLog, AgentRun):
        assert count(db, cls, user_id=UID) == 0, cls.__name__


def test_unknown_profile(db, capsys):
    assert main(["--profile", "nope"], session_factory=db) == 2
    assert "overspend-one-time" in capsys.readouterr().out



def test_seed_start_default(db, capsys):
    assert main(["--profile", PID, "--start", "default"], session_factory=db) == 0
    with db() as s:
        assert s.get(User, UID).prefs_json["sim"] == {"profile": PID, "date": "2026-09-30"}
    assert f"python scripts/simulate.py --user {UID} --days 30" in capsys.readouterr().out


# the style goes in before the first budget, so it's fitted that way (tpc-22)
def test_seed_style_503020(db, capsys):
    assert main(["--profile", PID, "--style", "50_30_20"], session_factory=db) == 0
    with db() as s:
        assert s.get(User, UID).prefs_json["budget"]["style"] == "50_30_20"
        period = initialize_budget(s, UID, date(2026, 10, 20))
        assert {ln.category: ln.limit_cents for ln in period.lines}["SAVINGS_TRANSFER"] == 84000


def test_replace_deletes_category_rules(db):
    main(["--profile", PID], session_factory=db)
    with db() as s:
        s.add(CategoryRule(user_id=UID, merchant_name="corner shop", category="FREE"))
        s.commit()
    assert main(["--profile", PID, "--replace"], session_factory=db) == 0
    assert count(db, CategoryRule, user_id=UID) == 0


# tpc-23: the goal graph's user can be seeded by name, and the hint points at the goal graph
def test_seed_goal_user(db, capsys):
    assert main(["--profile", "goals-card-24"], session_factory=db) == 0
    out = capsys.readouterr().out
    assert "goal_graph --user budget-goals-card-24 --date 2026-11-02" in out
    assert count(db, Liability, user_id="budget-goals-card-24") == 2


def test_replace_deletes_goals(db):
    main(["--profile", PID], session_factory=db)
    uid = f"budget-{PID}"
    with db() as s:
        s.add(Goal(user_id=uid, kind="savings", priority=1, monthly_cents=100, target_cents=1200, rationale="x"))
        s.commit()
    assert main(["--profile", PID, "--replace"], session_factory=db) == 0
    assert count(db, Goal, user_id=uid) == 0


def test_replace_graph_backed_user_with_foreign_keys(db, tmp_path, monkeypatch):
    from sqlalchemy import text
    from app import config
    from app.db.models import KGEdge, KGNode
    from app.kg.store import assert_edge, upsert_node
    from tests.fakes import SchemaReplyModel

    with db() as s:
        s.execute(text("PRAGMA foreign_keys=ON"))
        assert s.scalar(text("PRAGMA foreign_keys")) == 1
    monkeypatch.setattr(config, "KG_MODE", "on")
    main(["--profile", PID], session_factory=db)
    run_budget(db, UID, date(2026, 10, 20), model=SchemaReplyModel(), audit_path=tmp_path / "a.jsonl")
    assert count(db, KGEdge, user_id=UID) > 0
    with db() as s:
        concept = upsert_node(s, user_id=None, kind="concept", key="emergency-fund")
        rule = upsert_node(s, user_id=None, kind="concept", key="savings-rule")
        shared = assert_edge(s, user_id=None, src_id=concept.id, dst_id=rule.id, rel="HAS_RULE",
                             value={"text": "Save for emergencies"}, valid_from=date(2026, 1, 1))
        # An owned bridge points into shared guidance too.
        account = s.query(KGNode).filter_by(user_id=UID, kind="account").first()
        assert_edge(s, user_id=UID, src_id=account.id, dst_id=concept.id, rel="IS_A", value=None,
                    valid_from=date(2026, 1, 1))
        shared_id, concept_id = shared.id, concept.id
        s.commit()
    assert main(["--profile", PID, "--replace"], session_factory=db) == 0
    assert count(db, KGEdge, user_id=UID) == count(db, KGNode, user_id=UID) == 0
    assert count(db, Transaction, user_id=UID) == N
    with db() as s:
        assert s.get(KGEdge, shared_id).value_json == {"text": "Save for emergencies"}
        assert s.get(KGNode, concept_id).user_id is None
        assert s.execute(text("PRAGMA foreign_key_check")).all() == []
