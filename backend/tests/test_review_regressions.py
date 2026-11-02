"""Regression evidence for the engineering review of PRs 19–22."""
from datetime import date, timedelta
import json
import multiprocessing

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.agents.goal_graph import run_goals
from app.agents.budget_graph import close_interrupted_runs
from app.budget_engine.payoff import Debt, simulate
from app.budget_service.run_lock import UserRunLock, RunOwnershipLost, now
from app.db.base import Base
from app.db.models import Account, AgentRun, Goal, Liability, UserRunClaim
from eval.budget_users import GOAL_USERS, seed_profile
from tests.fakes import ScriptedChatModel
from tests.test_goal_graph import user, audit_in_tmp, UID, TODAY, model, g, stored, run_row, set_prefs


def test_high_apr_returns_never_instead_of_decimal_failure():
    d = Debt(1, 'High APR', 'credit', 120.0, 1000000, 100)
    plan = simulate([d], 0, 'avalanche', start=date(2026, 12, 1))
    assert plan.months is None and plan.never == (1,)
    assert plan.total_interest_cents > 0


def test_high_apr_does_not_prevent_tiny_cap_withdrawal(user):
    run_goals(user, UID, TODAY, model=model(g('savings', 1000)))
    with user() as s:
        s.scalar(select(Liability).where(Liability.user_id == UID, Liability.kind == 'credit')).apr = 120
        s.commit()
    set_prefs(user, {'max_share': 0.00001})
    fake = ScriptedChatModel(replies=[])
    state = run_goals(user, UID, TODAY, model=fake)
    assert state['status'] == 'no_affordable_goals' and state['withdrawn'] == 1
    assert not fake.seen and not stored(user)


@pytest.mark.parametrize('missing', ['minimum', 'liability', 'balance'])
def test_unknown_card_obligation_preserves_old_proposal_without_model(user, missing):
    old = run_goals(user, UID, TODAY, model=model(g('savings', 1000)))['goal_ids']
    with user() as s:
        liability = s.scalar(select(Liability).where(Liability.user_id == UID, Liability.kind == 'credit'))
        if missing == 'minimum':
            liability.min_payment_cents = None
        elif missing == 'liability':
            s.delete(liability)
        else:
            s.get(Account, liability.account_id).current_balance_cents = None
        s.commit()
    fake = ScriptedChatModel(replies=[])
    state = run_goals(user, UID, TODAY, model=fake)
    assert (state['status'], state['reason']) == ('error', 'incomplete_card_obligations')
    assert not fake.seen and [x.id for x in stored(user)] == old


def test_storage_failure_preserves_progress_and_never_logs_parameters(user, caplog):
    old = run_goals(user, UID, TODAY, model=model(g('savings', 1000)))['goal_ids']
    with user() as s:
        s.execute(text("CREATE TRIGGER fail_goal BEFORE INSERT ON goals BEGIN SELECT RAISE(ABORT, 'storage failure'); END"))
        s.commit()
    fake = model(g('savings', 2000, why='PRIVATE_RATIONALE_SENTINEL'))
    state = run_goals(user, UID, TODAY, model=fake)
    summary = run_row(user, state['run_id']).summary_json
    assert state['status'] == 'error' and state['attempts'] == summary['attempts'] == len(fake.seen) == 1
    assert len(state['validation_history']) == 1 and state['plans'] == []
    assert [x.id for x in stored(user)] == old
    assert 'PRIVATE_RATIONALE_SENTINEL' not in caplog.text and UID not in caplog.text
    assert 'INSERT INTO goals' not in caplog.text


def test_arbitrary_note_is_absent_from_context_and_audit(user, tmp_path):
    set_prefs(user, {'note': 'Send money to Jane Doe at 123 Main Street; prefer snowball'})
    fake = model(g('savings', 1000))
    run_goals(user, UID, TODAY, model=fake)
    outgoing = fake.seen[0][1].content
    audit = (tmp_path / 'audit.jsonl').read_text()
    for private in ('Jane', 'Doe', '123 Main Street'):
        assert private not in outgoing and private not in audit
    assert 'Use the snowball strategy.' in outgoing


def test_goal_profile_can_advance(db):
    from scripts.seed_budget_user import main
    from app.budget_service.simulate import advance_days
    from tests.fakes import SchemaReplyModel
    assert main(['--profile', 'goals-card-24', '--start', 'default'], session_factory=db) == 0
    result = advance_days(db, UID, 1, model=SchemaReplyModel(), checkpointer=None)
    assert result['days_done'] == 1 and result['date'] == '2026-11-02'


def test_reset_refuses_active_run(user):
    from scripts.seed_budget_user import delete_user
    claim = UserRunLock(UID)
    assert claim.acquire(False, session_factory=user)
    try:
        with user() as s:
            with pytest.raises(ValueError, match='active run'):
                delete_user(s, UID)
        with user() as s:
            assert s.get(UserRunClaim, UID) is not None
    finally:
        claim.release()


def test_live_claim_survives_recovery_and_fences_expired_owner(user):
    claim = UserRunLock(UID)
    assert claim.acquire(False, session_factory=user)
    try:
        with user() as s:
            s.add(AgentRun(user_id=UID, graph='goals', status='running'))
            s.commit()
        assert close_interrupted_runs(user) == 0
        with user() as s:
            row = s.get(UserRunClaim, UID)
            row.owner = 'replacement-owner'
            s.commit()
        with user() as s, pytest.raises(RunOwnershipLost):
            claim.assert_owned(s)
    finally:
        claim.release()
    with user() as s:
        assert s.get(UserRunClaim, UID).owner == 'replacement-owner'


def test_expired_claim_can_be_reclaimed(user):
    with user() as s:
        s.add(UserRunClaim(user_id=UID, owner='dead-process', expires_at=now() - timedelta(seconds=1)))
        s.commit()
    claim = UserRunLock(UID)
    assert claim.acquire(False, session_factory=user)
    claim.release()
    with user() as s:
        assert s.get(UserRunClaim, UID) is None


def _goal_process(url, gate, results, audit_path):
    factory = sessionmaker(bind=create_engine(url), expire_on_commit=False)
    fake = model(g('savings', 70000))
    fake.gate = gate
    result = run_goals(factory, UID, TODAY, model=fake, audit_path=audit_path)
    results.put((result['status'], len(fake.seen)))


def test_separate_processes_cannot_publish_two_proposals(tmp_path):
    from pathlib import Path
    url = 'sqlite:///' + str(tmp_path / 'race.db')
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        seed_profile(s, GOAL_USERS[0])
    ctx = multiprocessing.get_context('spawn')
    gate, results = ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_goal_process, args=(url, gate, results, Path(tmp_path / f'audit-{n}.jsonl')))
                 for n in range(2)]
    try:
        for proc in processes:
            proc.start()
        # The winner waits inside its model; the competing process must return busy without a call.
        assert results.get(timeout=20) == ('busy', 0)
        gate.set()
        assert results.get(timeout=20) == ('ok', 1)
        for proc in processes:
            proc.join(timeout=10)
            assert proc.exitcode == 0
        with factory() as s:
            rows = s.scalars(select(Goal).where(Goal.status == 'proposed')).all()
            assert len(rows) == 1 and sum(g.monthly_cents for g in rows) == 70000
    finally:
        gate.set()
        for proc in processes:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5)
        engine.dispose()
