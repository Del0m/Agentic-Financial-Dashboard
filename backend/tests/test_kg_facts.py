import json
from datetime import date

import pytest
from sqlalchemy import select

from app import config
from app.agents.budget_llm import (DECIDE_PROMPT, DIAGNOSE_PROMPT, Diagnosis, GraphDiagnosis, decide_messages,
                                   diagnose_messages, diagnosis_schema)
from app.budget_service.simulate import advance_days
from app.db.models import Alert, CategoryRule
from app.kg import facts as kg_facts
from app.kg import project
from eval.budget_users import PLANTED, profile_by_id, seed_profile
from eval.fixtures import temp_db
from tests.fakes import SchemaReplyModel


@pytest.fixture(autouse=True)
def audit_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROMPT_AUDIT_PATH", tmp_path / "audit.jsonl")


def graph_of(pid, until, today, merchants, category, *, rule=None):
    with temp_db() as Session, Session() as s:
        uid = seed_profile(s, profile_by_id(pid), until=until)
        project.backfill(s, uid, until)
        if rule:
            s.add(CategoryRule(user_id=uid, merchant_name=rule[0], category=rule[1]))
            s.flush()
            project.project_rules(s, uid, today)
        project.project_month(s, uid, today, today)
        s.flush()
        return kg_facts.graph_facts(s, uid, category, merchants, today.replace(day=1), today)


def test_a_merchant_from_last_december_shows_as_seasonal_history():
    g = graph_of("overspend-seasonal-13mo", date(2026, 12, 5), date(2026, 12, 5), ["Toy Barn"], "GENERAL_MERCHANDISE")
    [toy] = g["merchants"]
    assert toy["active_same_month_last_year"] is True and toy["months_active"] == 1
    assert toy["first_seen"] == "2025-12-01"
    assert g["same_month_last_year_cents"] > 0


def test_a_rule_this_month_shows_as_a_move_with_its_amount():
    g = graph_of("reclassified-by-rule", date(2026, 10, 7), date(2026, 10, 8), ["Lunchbox Co"], "FOOD_AND_DRINK",
                 rule=("lunchbox co", "FOOD_AND_DRINK"))
    [move] = g["rule_moves"]
    assert move["since"] == "2026-10-08" and move["moved_cents"] == 40000


def test_a_price_rise_shows_as_a_price_change():
    profile = profile_by_id("stream-price-increase")
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile, until=date(2026, 10, 2))
        advance_days(Session, uid, 1, model=SchemaReplyModel(), checkpointer=None)
        with Session() as s:
            g = kg_facts.graph_facts(s, uid, "ENTERTAINMENT", ["Streamly Plus"], date(2026, 10, 1), date(2026, 10, 3))
    assert g["price_changes"] == [{"merchant": "Streamly Plus", "from_cents": 9000, "to_cents": 13500, "since": "2026-10-03"}]


def test_dismissed_cuts_show_in_the_advice_history():
    with temp_db() as Session, Session() as s:
        uid = seed_profile(s, profile_by_id("previously-dismissed"), until=date(2026, 10, 8))
        project.backfill(s, uid, date(2026, 10, 8))
        g = kg_facts.graph_facts(s, uid, "FOOD_AND_DRINK", [], date(2026, 10, 1), date(2026, 10, 9))
    assert [(a["month"], a["action"], a["outcome"]) for a in g["advice_history"]] == [
        ("2026-08-01", "suggest_cuts", "dismissed"), ("2026-09-01", "suggest_cuts", "dismissed")]
    assert kg_facts.cut_was_dismissed(g) is True


def test_one_dismissal_isnt_enough():
    one = {"advice_history": [{"action": "suggest_cuts", "outcome": "dismissed"}, {"action": "alert", "outcome": "dismissed"}]}
    assert kg_facts.cut_was_dismissed(one) is False
    assert kg_facts.cut_was_dismissed(None) is False


# the ablation's control: with the graph off, nothing the model receives changes
def test_with_the_graph_off_prompts_and_schema_are_unchanged(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "off")
    assert diagnose_messages("ctx")[0].content == DIAGNOSE_PROMPT
    assert decide_messages("ctx", None, 100, 100, "medium")[0].content == DECIDE_PROMPT
    assert diagnosis_schema() is Diagnosis


def test_with_the_graph_on_the_prompts_add_to_the_old_ones(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "on")
    assert diagnose_messages("ctx")[0].content.startswith(DIAGNOSE_PROMPT + "\n\nThe facts also have graph_facts")
    assert decide_messages("ctx", None, 100, 100, "medium")[0].content.startswith(DECIDE_PROMPT)
    assert diagnosis_schema() is GraphDiagnosis


def _run(pid, until, days, _kg, tmp_model):
    with temp_db() as Session:
        with Session() as s:
            uid = seed_profile(s, profile_by_id(pid), until=until)
        advance_days(Session, uid, days, model=tmp_model, checkpointer=None)
        with Session() as s:
            return s.scalars(select(Alert).where(Alert.user_id == uid, Alert.kind == "overspend").order_by(Alert.id)).all()


def test_graph_facts_reach_the_prompt_only_when_on(monkeypatch):
    for kg, present in (("off", False), ("on", True)):
        monkeypatch.setattr(config, "KG_MODE", kg)
        model = SchemaReplyModel()
        _run("previously-dismissed", date(2026, 10, 8), 1, kg, model)
        texts = [str(m.content) for call in model.seen for m in call]
        assert any("graph_facts\":" in t for t in texts) is present, kg


def test_graph_facts_leave_out_account_details(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "on")
    model = SchemaReplyModel()
    _run("reclassified-by-rule", date(2026, 10, 6), 2, "on", model)
    text = json.dumps([str(m.content) for call in model.seen for m in call])
    assert "plaid_account" not in text and "budget-reclassified" not in text


def test_reclassified_can_only_alert(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "on")
    alerts = _run("reclassified-by-rule", date(2026, 10, 6), 2, "on",
                  SchemaReplyModel(cause="reclassified", first_action="suggest_cuts"))
    assert alerts and all(a.action == "alert" and a.cause == "reclassified" for a in alerts if a.category == "FOOD_AND_DRINK")


def test_a_cut_dismissed_twice_isnt_proposed_again(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "on")
    alerts = _run("previously-dismissed", date(2026, 10, 8), 1, "on", SchemaReplyModel(cause="one_time", first_action="suggest_cuts"))
    new = [a for a in alerts if a.category == "FOOD_AND_DRINK" and a.status == "pending_user"]
    assert new and all(a.action == "alert" for a in new)


def test_with_the_graph_off_the_guard_doesnt_apply(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "off")
    alerts = _run("previously-dismissed", date(2026, 10, 8), 1, "off", SchemaReplyModel(cause="one_time", first_action="suggest_cuts"))
    assert any(a.action == "suggest_cuts" for a in alerts if a.category == "FOOD_AND_DRINK" and a.status == "pending_user")


def test_the_four_existing_planted_users_are_unchanged_with_the_graph_off(monkeypatch):
    monkeypatch.setattr(config, "KG_MODE", "off")
    assert [p.id for p in PLANTED] == ["overspend-one-time", "overspend-recurring", "overspend-seasonal", "overspend-known-merchant"]
    assert all(not p.rules and not p.dismissed for p in PLANTED)
