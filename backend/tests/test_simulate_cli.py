from datetime import date

import pytest

from app import config
from app.budget_service.daily import user_lock
from app.db.models import User
from eval.budget_users import PLANTED, seed_profile
from scripts.simulate import main
from tests.fakes import SchemaReplyModel


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "BUDGET_CHECKPOINT_PATH", tmp_path / "ck.db")


def seed(db, until=date(2026, 10, 9)):
    with db() as s:
        return seed_profile(s, next(p for p in PLANTED if p.id == "overspend-one-time"), until=until)


def test_cli_prints_days_and_summary(db, capsys):
    uid = seed(db)
    assert main(["--user", uid, "--days", "3"], session_factory=db, model=SchemaReplyModel()) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    day_lines = [ln for ln in lines if ln.startswith("2026-10-")]
    assert [ln.split()[0] for ln in day_lines] == ["2026-10-10", "2026-10-11", "2026-10-12"]
    assert "+1 txns" in day_lines[2] and "flags: GENERAL_MERCHANDISE" in day_lines[2]
    assert lines[-1].startswith("simulated 3 day(s): 2026-10-09 → 2026-10-12, 3 runs, ")


def test_cli_not_simulated_exit_1(db, capsys):
    uid = seed(db, until=None)
    assert main(["--user", uid, "--days", "1"], session_factory=db, model=SchemaReplyModel()) == 1
    assert "not a simulated user; seed it with --start" in capsys.readouterr().out


def test_cli_locked_exit_1(db, capsys):
    uid = seed(db)
    lock = user_lock(uid)
    lock.acquire()
    try:
        assert main(["--user", uid, "--days", "1"], session_factory=db, model=SchemaReplyModel()) == 1
    finally:
        lock.release()
    assert "A budget run is already in progress." in capsys.readouterr().out


def test_simulate_sets_style(db, capsys):
    uid = seed(db)
    assert main(["--user", uid, "--days", "1", "--style", "50_30_20"], session_factory=db,
                model=SchemaReplyModel()) == 0
    with db() as s:
        assert s.get(User, uid).prefs_json["budget"]["style"] == "50_30_20"
    with pytest.raises(SystemExit) as exit_:
        main(["--user", uid, "--days", "1", "--style", "envelopes"], session_factory=db, model=SchemaReplyModel())
    assert exit_.value.code == 2


# review on #14: the cli's lock is per-process, so it also refuses while another process has a run going
def test_cli_refuses_while_another_process_runs(db, capsys):
    from app.db.models import AgentRun
    uid = seed(db)
    with db() as s:
        s.add(AgentRun(user_id=uid, graph="simulate", status="running"))
        s.commit()
    assert main(["--user", uid, "--days", "1"], session_factory=db, model=SchemaReplyModel()) == 1
    assert "already in progress" in capsys.readouterr().out


# a row left "running" by a process that died hours ago doesn't block the cli forever
def test_cli_ignores_a_stale_running_row(db, capsys):
    from datetime import datetime, timedelta, timezone

    from app.db.models import AgentRun
    uid = seed(db, until=None)
    with db() as s:
        s.add(AgentRun(user_id=uid, graph="budget", status="running",
                       started_at=datetime.now(timezone.utc) - timedelta(hours=3)))
        s.commit()
    assert main(["--user", uid, "--days", "1"], session_factory=db, model=SchemaReplyModel()) == 1
    assert "not a simulated user" in capsys.readouterr().out          # got past the guard


# review on #14: the cli uses the app's checkpointer instead of building its own
def test_cli_uses_the_app_checkpointer(db, monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import scripts.simulate as cli
    from app.api import budget_hooks
    saver, seen = InMemorySaver(), []
    monkeypatch.setattr(budget_hooks, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(cli, "advance_days", lambda *a, **kw: seen.append(kw["checkpointer"]) or {
        "days_done": 0, "start_date": "x", "date": "x", "run_ids": [], "alerts_written": 0})
    main(["--user", seed(db), "--days", "1"], session_factory=db, model=SchemaReplyModel())
    assert seen == [saver]
