from pathlib import Path

import pytest
from sqlalchemy import select

from app import config
from app.db.models import KGEdge, KGNode, User
from app.kg import project
from app.kg.retrieve import gather
from eval.budget_users import PLANTED, seed_profile
from scripts import setup_graph

GUIDANCE = Path(__file__).resolve().parents[2] / "kb" / "graph.json"
PROFILE = next(p for p in PLANTED if p.id == "overspend-recurring")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


@pytest.fixture
def seeded(db):
    with db() as s:
        return seed_profile(s, PROFILE)


def _edges(db) -> list[int]:
    with db() as s:
        return sorted(s.scalars(select(KGEdge.id).where(KGEdge.expired_at.is_(None))))


def test_loads_guidance_and_each_users_history(db, seeded):
    assert setup_graph.main(db, GUIDANCE) == 0
    with db() as s:
        assert s.scalar(select(KGNode.id).where(KGNode.user_id.is_(None), KGNode.kind == "concept").limit(1)) is not None
        assert project.backfill_complete(s, seeded)
        today = project.current_day(s, seeded)
        roth = gather(s, seeded, "What was the Roth IRA contribution limit in 2024?", today)
        gym = gather(s, seeded, "When did my gym membership start?", today)
    assert any("$7,000.00 for 2024" in f.line for f in roth.guidance)
    assert any("FitClub Gym" in f.line for f in gym.user)


# every docker compose up runs it again
def test_running_again_changes_nothing(db, seeded):
    setup_graph.main(db, GUIDANCE)
    before = _edges(db)
    assert setup_graph.main(db, GUIDANCE) == 0
    assert _edges(db) == before


def test_a_finished_history_isnt_rebuilt(db, seeded, monkeypatch):
    setup_graph.main(db, GUIDANCE)
    monkeypatch.setattr(project, "backfill", lambda *a, **k: pytest.fail("rebuilt a finished history"))
    assert setup_graph.main(db, GUIDANCE) == 0


def test_missing_guidance_still_builds_histories(db, seeded, tmp_path, capsys):
    assert setup_graph.main(db, tmp_path / "graph.json") == 0
    assert "wasn't loaded" in capsys.readouterr().out
    with db() as s:
        assert project.backfill_complete(s, seeded)


def test_unreadable_guidance_doesnt_stop_startup(db, seeded, tmp_path, capsys):
    path = tmp_path / "graph.json"
    path.write_text("not json")
    assert setup_graph.main(db, path) == 0
    assert "guidance graph not loaded" in capsys.readouterr().out
    with db() as s:
        assert project.backfill_complete(s, seeded)


def test_one_users_failure_doesnt_stop_the_others(db, seeded, monkeypatch, capsys):
    with db() as s:
        s.add(User(id="another", email="another@example.test", password_hash="!"))
        s.commit()
    real = project.backfill

    def fails_for_seeded(session, user_id, today):
        if user_id == seeded:
            raise ValueError("boom")
        return real(session, user_id, today)

    monkeypatch.setattr(project, "backfill", fails_for_seeded)
    assert setup_graph.main(db, GUIDANCE) == 0
    assert f"graph for {seeded} not built: ValueError" in capsys.readouterr().out
    with db() as s:
        assert project.backfill_complete(s, "another") and not project.backfill_complete(s, seeded)
