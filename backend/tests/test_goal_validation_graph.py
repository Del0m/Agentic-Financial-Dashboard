import json

import pytest
from langchain_core.messages import AIMessage

from app import config
from app.agents.goal_graph import run_goals
from app.api import budget_hooks
from app.db.models import Account
from tests.fakes import ScriptedChatModel, ollama_has
from tests.test_goal_graph import (TODAY, UID, accept, audit_in_tmp, cli, g, model, run_row, set_prefs, stored, user)


def replies(*proposals):
    return ScriptedChatModel(replies=[AIMessage(content=p if isinstance(p, str) else json.dumps({'goals': p}))
                                      for p in proposals])


def test_tight_surplus_retries_to_affordable_dated_goals(user, tmp_path):
    set_prefs(user, {'max_share': 0.05, 'note': "Jane Doe's card first"})
    fake = replies([g('debt_payoff', 4000, 'avalanche', 'Jane Doe secret'), g('savings', 1000)],
                   [g('debt_payoff', 4000, 'avalanche'), g('savings', 859)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok'
    assert result['surplus_cents'] == 97175 and result['cap_cents'] == 4859
    assert result['attempts'] == len(fake.seen) == 2
    goals = stored(user)
    assert sum(x.monthly_cents for x in goals) == 4859
    assert all(x.target_date is not None for x in goals)
    assert result['validation_issues'] == []
    assert result['validation_history'][0]['issues'][0]['code'] == 'over_cap'
    summary = run_row(user, result['run_id']).summary_json
    assert summary['attempts'] == 2 and summary['validation_history'] == result['validation_history']
    first = json.loads(fake.seen[0][1].content.split('\n', 1)[1])
    correction_text = fake.seen[1][1].content.split('\n', 1)[1]
    second = json.loads(correction_text)
    assert first['data'] == second['data'] and first['user'] == second['user']
    assert second['correction']['attempt'] == 2
    assert second['correction']['rejected_goals'][0] == {
        'kind': 'debt_payoff', 'monthly_dollars': 40.0, 'strategy': 'avalanche'}
    assert '$50.00' in correction_text and '$48.59' in correction_text and '$1.41' in correction_text
    assert 'Jane' not in correction_text and 'secret' not in correction_text and UID not in correction_text
    audit = [json.loads(x) for x in (tmp_path / 'audit.jsonl').read_text().splitlines()]
    assert len(audit) == 2 and audit[1]['prompt'] == correction_text
    assert audit[1]['params']['attempt'] == 2


def test_tight_surplus_exhaustion_preserves_previous_proposal(user):
    old = run_goals(user, UID, TODAY, model=model(g('savings', 5000)))['goal_ids']
    set_prefs(user, {'max_share': 0.05})
    fake = replies([g('savings', 5000)], [g('savings', 6000)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'error' and result['reason'] == 'validation_failed'
    assert result['attempts'] == len(fake.seen) == 2
    assert result['goal_ids'] == [] and result.get('plans', []) == []
    assert [x.id for x in stored(user)] == old and stored(user, 'superseded') == []
    assert [x['code'] for x in result['validation_issues']] == ['over_cap']
    assert run_row(user, result['run_id']).status == 'error'


@pytest.mark.parametrize('bad_first', [True, False])
def test_parsing_and_affordability_share_two_calls(user, bad_first):
    bad, over = 'private invalid Jane Doe output', [g('savings', 100000)]
    fake = replies(*( [bad, over] if bad_first else [over, bad]))
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'error' and result['goal_ids'] == [] and stored(user) == []
    assert result['attempts'] == len(fake.seen) == 2
    assert len(result['validation_history']) == 2
    assert 'Jane' not in json.dumps(result['validation_history'])
    if bad_first:
        context = json.loads(fake.seen[1][1].content.split('\n', 1)[1])
        assert context['correction']['rejected_goals'] == []
        assert 'Jane' not in fake.seen[1][1].content


def test_malformed_then_valid_uses_one_correction(user):
    fake = replies('not json', [g('savings', 10000)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok' and result['attempts'] == len(fake.seen) == 2


@pytest.mark.parametrize('bad', [[g('savings', 100), g('savings', 200)], [g('emergency_fund', 100)]])
def test_invalid_kind_or_missing_date_retries_whole_proposal(user, bad):
    fake = replies(bad, [g('savings', 1000)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok' and result['attempts'] == 2
    assert [(x.kind, x.monthly_cents) for x in stored(user)] == [('savings', 1000)]


def test_preference_excluded_kind_retries_without_partial_storage(user):
    set_prefs(user, {'skip_kinds': ['savings']})
    fake = replies([g('savings', 5000), g('emergency_fund', 20000)], [g('debt_payoff', 20000)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok' and len(fake.seen) == 2
    assert [x.kind for x in stored(user)] == ['debt_payoff']
    assert result['validation_history'][0]['issues'][0]['code'] == 'not_offered'


def test_all_computable_failures_reported_together(user):
    fake = replies([g('savings', 80000), g('savings', 100), g('emergency_fund', 100)], [g('savings', 5000)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok'
    assert {x['code'] for x in result['validation_history'][0]['issues']} == {'repeat', 'over_cap', 'no_date'}


def test_debt_snapshot_does_not_reload_during_correction(user):
    from app.agents import goal_graph
    original = goal_graph.invoke_structured
    calls = []

    def changing_model(*args, **kwargs):
        if not calls:
            with user() as s:
                s.query(Account).filter_by(user_id=UID, type='credit').update({'current_balance_cents': 9999999})
                s.commit()
        calls.append(1)
        return original(*args, **kwargs)

    # The database changes after load_facts; neither build nor correction may reread it.
    from unittest.mock import patch
    fake = replies([g('debt_payoff', 100000)], [g('debt_payoff', 20000)])
    with patch.object(goal_graph, 'invoke_structured', changing_model):
        result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok' and stored(user)[0].target_cents == 2160000
    a, b = [json.loads(call[1].content.split('\n', 1)[1]) for call in fake.seen]
    assert a['data'] == b['data']


def test_outage_after_rejection_keeps_evidence_and_no_third_call(user):
    fake = replies([g('savings', 100000)])
    fake.replies.append(ConnectionError('private outage text'))
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'error' and result['reason'] == 'ConnectionError'
    assert result['attempts'] == len(fake.seen) == 2
    assert len(result['validation_history']) == 1 and stored(user) == []
    assert 'private' not in json.dumps(run_row(user, result['run_id']).summary_json)


@pytest.mark.parametrize('cap', [0, 99])
def test_cap_below_minimum_withdraws_without_model(user, cap, capsys):
    run_goals(user, UID, TODAY, model=model(g('savings', 5000)))
    set_prefs(user, {'max_share': max(cap, 0.01) / 97175})
    fake = ScriptedChatModel(replies=[])
    assert cli(user, fake=fake) == 0
    text = capsys.readouterr().out
    assert 'below the $1.00 minimum' in text and 'withdrawn' in text
    assert fake.seen == [] and stored(user) == [] and len(stored(user, 'superseded')) == 1
    with user() as s:
        from app.db.models import AgentRun
        run = s.query(AgentRun).order_by(AgentRun.id.desc()).first()
        assert run.status == 'ok' and run.summary_json['outcome'] == 'no_affordable_goals'
        assert run.summary_json['attempts'] == 0 and run.summary_json['cap_cents'] == cap


def test_exact_minimum_cap_allows_one_call(user):
    set_prefs(user, {'max_share': 100 / 97175})
    fake = replies([g('savings', 100)])
    result = run_goals(user, UID, TODAY, model=fake)
    assert result['status'] == 'ok' and result['attempts'] == 1
    assert result['cap_cents'] == stored(user)[0].monthly_cents == 100


def test_cli_failure_explains_exhaustion_and_preservation(user, capsys):
    run_goals(user, UID, TODAY, model=model(g('savings', 5000)))
    fake = replies([g('savings', 80000)], [g('savings', 80000)])
    assert cli(user, fake=fake) == 1
    text = capsys.readouterr().out
    assert '2 model calls' in text and '$800.00' in text and '$777.40' in text
    assert 'No replacement' in text and 'earlier proposal' in text
    assert '1. savings' not in text


def test_api_reflects_correction_preservation_and_withdrawal(user, client, monkeypatch):
    monkeypatch.setattr(config, 'DEV_USER_EMAIL', 'goals-card-24@eval.local')
    monkeypatch.setattr(budget_hooks, 'session_factory', user)      # GET /goals reads the hooks now
    old = run_goals(user, UID, TODAY, model=model(g('savings', 5000)))['goal_ids']
    set_prefs(user, {'max_share': 0.05})
    run_goals(user, UID, TODAY, model=replies([g('savings', 5000)], [g('savings', 6000)]))
    assert [x['id'] for x in client.get('/goals').json()] == old
    run_goals(user, UID, TODAY, model=replies([g('savings', 5000)], [g('savings', 4800)]))
    rows = client.get('/goals').json()
    assert len(rows) == 1 and rows[0]['monthly_cents'] == 4800 and rows[0]['id'] not in old
    assert rows[0]['target_date'] is not None
    set_prefs(user, {'max_share': 0.00001})
    run_goals(user, UID, TODAY, model=ScriptedChatModel(replies=[]))
    assert client.get('/goals').json() == []


@pytest.mark.skipif(not ollama_has(config.OLLAMA_MODEL), reason='needs Ollama with configured model')
@pytest.mark.parametrize('trial', range(1, 6))
def test_live_tight_surplus_trials(user, trial):
    set_prefs(user, {'max_share': 0.05})
    result = run_goals(user, UID, TODAY)
    rows = stored(user)
    print(f"trial={trial} status={result['status']} attempts={result.get('attempts')} "
          f"cap={result.get('cap_cents')} total={sum(x.monthly_cents for x in rows)} "
          f"dates={[str(x.target_date) for x in rows]}")
    assert result['status'] == 'ok', result
    assert (result['surplus_cents'], result['cap_cents']) == (97175, 4859)
    assert 1 <= result['attempts'] <= 2
    assert rows and sum(x.monthly_cents for x in rows) <= 4859
    assert all(x.target_date is not None for x in rows)


# the cap is what's left after accepted goals, so a retry can't spend their money either
def test_new_goal_cannot_spend_accepted_money(user):
    accept(user, "debt_payoff", 58300)
    over = model(g("emergency_fund", 19441))
    over.replies.append(over.replies[0])
    r = run_goals(user, UID, TODAY, model=over)
    assert r["status"] == "error" and any(i["code"] == "over_cap" for i in r["validation_issues"])
    assert stored(user) == []
