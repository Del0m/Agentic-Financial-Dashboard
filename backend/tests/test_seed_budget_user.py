import json
from datetime import date

from langchain_core.messages import AIMessage

from app.agents.budget_graph import run_budget
from app.budget_service.initialize import initialize_budget
from app.db.models import AdviceLog, AgentRun, Alert, BudgetPeriod, CategoryRule, Transaction, User
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
