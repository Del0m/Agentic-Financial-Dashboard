import pytest

from app import config
from app.db.models import BudgetPeriod, Transaction, User
from app.budget_service.daily import sim_today
from eval.budget_users import PLANTED, seed_profile
from scripts.setup_demo import DEMO_PROFILE, DEMO_USER, main
from tests.fakes import SchemaReplyModel


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(config, "BUDGET_CHECKPOINT_PATH", tmp_path / "ck.db")


def counts(db):
    with db() as s:
        return s.query(Transaction).filter_by(user_id=DEMO_USER).count(), \
            s.query(BudgetPeriod).filter_by(user_id=DEMO_USER).count()


# a raising model proves the first day needs no llm, so setup works before ollama is up
class NoModel(SchemaReplyModel):
    def invoke(self, *args, **kwargs):
        raise AssertionError("the demo setup must not call the model")


def test_seeds_the_user_with_an_open_budget_and_no_llm(db):
    assert main(session_factory=db, model=NoModel()) == 0
    with db() as s:
        assert s.get(User, DEMO_USER) is not None
        day = sim_today(s, DEMO_USER)
        period = s.query(BudgetPeriod).filter_by(user_id=DEMO_USER).one()
    assert period.start_date <= day <= period.end_date


# every docker compose up runs it again, and that mustn't undo the days the viewer has advanced
def test_running_again_changes_nothing(db):
    main(session_factory=db, model=NoModel())
    with db() as s:
        day = sim_today(s, DEMO_USER)
    before = counts(db)
    assert main(session_factory=db, model=NoModel()) == 0
    with db() as s:
        assert sim_today(s, DEMO_USER) == day
    assert counts(db) == before


def test_a_seed_interrupted_before_the_budget_is_finished(db):
    with db() as s:
        seed_profile(s, next(p for p in PLANTED if p.id == DEMO_PROFILE), until=None)
        user = s.get(User, DEMO_USER)
        user.prefs_json = {"sim": {"profile": DEMO_PROFILE, "date": "2026-09-30"}}
        s.commit()
    assert main(session_factory=db, model=NoModel()) == 0
    assert counts(db)[1] == 1


# a user seeded by hand isn't simulated, and might hold someone's work
def test_a_hand_seeded_user_is_left_alone(db, capsys):
    with db() as s:
        seed_profile(s, next(p for p in PLANTED if p.id == DEMO_PROFILE), until=None)
    before = counts(db)
    assert main(session_factory=db, model=NoModel()) == 0
    assert counts(db) == before
    assert "left as it is" in capsys.readouterr().out
