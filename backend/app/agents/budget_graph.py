import argparse
import json
import logging
import sqlite3
import sys
import threading
from collections import Counter
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Callable, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.budget_llm import (_RANK, Action, GraphDiagnosis, decide_messages, decide_signal, diagnose_messages,
                                  diagnosis_schema, invoke_structured)
from app.agents.budget_checkpoints import BudgetCheckpoints
from app import config
from app.agents.llm import get_chat_model
from app.budget_engine.diagnose import OverspendFacts, overspend_facts, proposed_limit_cents
from app.budget_engine.fit import HISTORY_MONTHS
from app import config
from app.kg import project
from app.kg.facts import cut_was_dismissed, graph_facts, has_content
from app.budget_engine.pace import BudgetLine as EngineLine, PaceResult, evaluate, history_start
from app.budget_engine.recurring import monthly_cents
from app.budget_engine.signals import (SIGNAL_CAP, PreviousDip, Signal, cancel_limit, choose, fallback_message,
                                       low_balance_key, move_amount, rank_signals, repeat_decision)
from app.budget_engine.txns import add_months
from app.budget_service.initialize import initialize_budget, load_txns
from app.budget_service.recurring import muted_stream_ids, refresh_streams, stored_streams
from app.budget_service.forecast_service import snapshot
from app.budget_service.forecast_data import forecast_accounts, start_balance
from app.db.models import Account, AdviceLog, AgentRun, Alert, BudgetPeriod, PlaidItem, utcnow
from app.privacy.scrub import scrub_merchant
from app.plaid_service.sync import sync_item
from app.privacy.gateway import PrivacyGateway

logger = logging.getLogger(__name__)

# the daily budget agent (architecture 6.1-6.2, spec: tpc-19). the budget engine does the maths; the llm
# names each overspend's cause from facts the code computed and picks an action. an llm that can't answer
# never hides an overspend: the graph falls back to a plain alert

SyncFn = Callable[[Session, str], dict]


# plain values only, so tpc-20 can add a checkpointer and interrupt() without reshaping it
class BudgetState(TypedDict, total=False):
    user_id: str
    today: str
    run_id: int
    period_id: Optional[int]
    status: str                 # running | ok | no_budget | period_ended | error
    sync: dict
    uncategorized: int
    results: list[dict]         # every PaceResult
    flags: list[dict]           # the flagged ones
    facts: dict[str, dict]      # category -> OverspendFacts, plus its proposed limit
    contexts: dict[str, str]    # category -> the gateway's prompt context, built (and audited) once
    memory: dict[str, list]     # category -> this month's earlier alerts, for the decide prompt's summary
    diagnoses: dict[str, Optional[dict]]
    actions: dict[str, dict]
    alert_ids: list[int]
    adjust_alert_ids: list[int]   # alerts that would change the budget, so the run waits for the user
    decisions: dict               # alert id -> accepted | dismissed, from the user
    waiting_for: list[int]
    llm_failures: int
    llm_calls: int
    llm_retries: int
    graph_error: bool
    signals: list[dict]
    signal_actions: dict[str, dict]
    signals_error: Optional[str]
    streams: dict               # counts and stream ids only, safe for the run summary and checkpoint
    forecast: dict              # dates and cents only; bill names and balances stay in transient signals


# default sync: every active plaid item the user has. one failing item doesn't stop the others
def plaid_sync(session: Session, user_id: str) -> dict:
    items = session.scalars(select(PlaidItem).where(PlaidItem.user_id == user_id,
                                                    PlaidItem.status == "active")).all()
    errors = []
    for item in items:
        try:
            sync_item(session, item)
        except Exception as exc:
            session.rollback()
            errors.append(type(exc).__name__)
    return {"items": len(items), "errors": errors}


def _severity(reason: str) -> str:
    return "high" if reason == "over_limit" else "medium"


# memory, read by code (tpc-20): this period's overspend alerts for the category, oldest first
def _this_month_alerts(s: Session, user_id: str, period_id: int, category: str) -> list[dict]:
    alerts = s.scalars(select(Alert).where(Alert.user_id == user_id, Alert.period_id == period_id,
                                           Alert.kind == "overspend", Alert.category == category)
                       .order_by(Alert.created_at, Alert.id))
    return [{"severity": a.severity, "action": a.action, "status": a.status} for a in alerts]


# the category's advice in this budget month and the 3 before it, newest first. keyed by the alert's budget
# month, not created_at: runs can be simulated on other dates than the wall clock
def _past_advice(s: Session, user_id: str, period_start: date, category: str) -> list[dict]:
    rows = s.execute(
        select(AdviceLog, Alert, BudgetPeriod)
        .join(Alert, AdviceLog.alert_id == Alert.id)
        .join(BudgetPeriod, Alert.period_id == BudgetPeriod.id)
        .where(AdviceLog.user_id == user_id, AdviceLog.category == category,
               BudgetPeriod.start_date >= add_months(period_start, -3), BudgetPeriod.start_date <= period_start)
        .order_by(BudgetPeriod.start_date.desc(), AdviceLog.id.desc())
        .limit(5)).all()
    return [{"month": p.start_date.strftime("%B %Y"), "action": a.action, "outcome": adv.outcome}
            for adv, a, p in rows]


# never stops a diagnosis: without the graph's facts, the model still has the rest
def _graph_facts_for(session_factory, user_id: str, facts: OverspendFacts, today: date) -> Optional[dict]:
    try:
        with session_factory() as s:
            return graph_facts(s, user_id, facts.category, [m.merchant for m in facts.merchants], facts.month, today)
    except Exception as exc:
        logger.warning("graph facts failed: %s", type(exc).__name__)
        return None


def _label(category: str) -> str:
    return category.replace("_", " ").capitalize()


# the message when the model can't decide: the plain numbers, written by code
def _fallback_message(flag: dict) -> str:
    spent, limit = flag["spent_cents"] / 100, flag["limit_cents"] / 100
    if flag["reason"] == "over_limit":
        return f"{_label(flag['category'])} is over its ${limit:,.2f} limit: ${spent:,.2f} so far."
    return (f"{_label(flag['category'])} is at {flag['pace']:.0%} of its pace: ${spent:,.2f} of ${limit:,.2f} "
            f"by day {flag['days_elapsed']}.")


def build_budget_graph(session_factory, *, model: Optional[BaseChatModel], gateway: Optional[PrivacyGateway],
                       sync_fn: Optional[SyncFn] = None, checkpointer: Optional[BaseCheckpointSaver] = None,
                       report: Optional[dict] = None):
    def today_of(state) -> date:
        return date.fromisoformat(state["today"])

    def load_state(state: BudgetState) -> dict:
        today = today_of(state)
        with session_factory() as s:
            period = initialize_budget(s, state["user_id"], today)
            if period is None or today < period.start_date:
                return {"status": "no_budget", "period_id": None}
            if today > period.end_date:
                return {"status": "period_ended", "period_id": period.id}   # closing it is tpc-22's job
            return {"period_id": period.id}

    def sync(state: BudgetState) -> dict:
        if sync_fn is None:
            return {"sync": {"skipped": True}}
        with session_factory() as s:
            try:
                return {"sync": sync_fn(s, state["user_id"])}
            except Exception as exc:
                # plaid being down shouldn't stop the budget check; it runs on the data already stored
                s.rollback()
                return {"sync": {"items": 0, "errors": [type(exc).__name__]}}

    def period_txns(s, state):
        period = s.get(BudgetPeriod, state["period_id"])
        return period, load_txns(s, state["user_id"], period.start_date, today_of(state))

    def categorize(state: BudgetState) -> dict:
        # llm categorization is a follow-up; for now the run only reports how much plaid left uncategorized
        with session_factory() as s:
            _, txns = period_txns(s, state)
        return {"uncategorized": sum(1 for t in txns if t.category == "UNCATEGORIZED")}

    def update_totals(state: BudgetState) -> dict:
        with session_factory() as s:
            period, txns = period_txns(s, state)
            lines = [EngineLine(ln.category, ln.kind, ln.limit_cents, ln.group) for ln in period.lines]
            history = load_txns(s, state["user_id"], history_start(period.start_date),
                                period.start_date - timedelta(days=1))
            results = evaluate(lines, txns, today_of(state), history)
            by_cat = {r.category: r for r in results}
            for ln in period.lines:
                ln.spent_cents, ln.last_pace = by_cat[ln.category].spent_cents, by_cat[ln.category].pace
            s.commit()
        return {"results": [asdict(r) for r in results]}

    # deterministic, and it never stops the run: the next run heals a failed projection (tpc-36)
    def update_graph(state: BudgetState) -> dict:
        with session_factory() as s:
            ok = project.safely(s, "update_graph", lambda: project.project_run(s, state["user_id"], today_of(state)))
            s.commit()
        return {} if ok else {"graph_error": True}

    def detect_overspend(state: BudgetState) -> dict:
        return {"flags": [r for r in state["results"] if r["flagged"]]}

    def detect_recurring(state: BudgetState) -> dict:
        try:
            with session_factory() as s:
                changes = refresh_streams(s, state["user_id"], today_of(state), state["run_id"])
                rows = {r.id: r for r in stored_streams(s, state["user_id"])}
                existing = set(s.scalars(select(Alert.signal_key).where(Alert.user_id == state["user_id"],
                                                                           Alert.signal_key.is_not(None))))
                signals = []
                summary = {"count": len(rows), "new": [], "price_up": [], "income_change": []}
                for change in changes:
                    key = f"{change.kind}|{change.stream_id}|{change.token}"
                    if key in existing:
                        continue
                    row = rows[change.stream_id]
                    numbers = {"direction": row.direction, "merchant": row.merchant_name,
                               "frequency": row.frequency, "typical_cents": row.typical_cents,
                               "last_cents": row.last_cents, "next_date": row.next_date.isoformat(), "status": row.status}
                    if change.previous_cents is not None:
                        numbers["previous_cents"] = change.previous_cents
                    signals.append(asdict(Signal(change.kind, key, row.id, row.category, numbers)))
                    summary[{"new_subscription": "new", "price_increase": "price_up",
                             "income_change": "income_change"}[change.kind]].append(row.id)
                s.commit()
            return {"signals": signals, "streams": summary}
        except Exception as exc:
            return {"signals": [], "signals_error": type(exc).__name__}

    def forecast_cash(state: BudgetState) -> dict:
        try:
            with session_factory() as s:
                snap = snapshot(s, state["user_id"], today_of(state))
                result, dip = snap.result, snap.result.dip
                summary = {"ok": result.status == "ok", "low_balance": None if dip is None else {
                    "date": dip.date.isoformat(), "shortfall_cents": dip.shortfall_cents},
                    "monthly_surplus_cents": result.monthly_surplus_cents,
                    "surplus_source": "forecast" if result.surplus_fallback is None else "measured",
                    "committed_goals_cents": result.committed_goal_cents, "assumed_no_income": result.income == "none"}
                signals = list(state.get("signals", []))
                if dip is not None:
                    bill_key = dip.cause.key if dip.cause else "everyday"
                    previous = s.scalars(select(Alert).where(Alert.user_id == state["user_id"], Alert.kind == "low_balance",
                        Alert.signal_key.startswith(f"low_balance|{bill_key}|", autoescape=True)).order_by(Alert.id.desc())).first()
                    prior = PreviousDip(date.fromisoformat(previous.signal_key.rsplit("|", 1)[1]),
                                        previous.signal_cents, previous.signal_days) if previous is not None else None
                    if repeat_decision(prior, dip.date, dip.shortfall_cents, dip.days_ahead):
                        low = min(d.low_cents for d in result.days[:14])
                        amount = move_amount(dip.shortfall_cents, snap.buffer_cents)
                        savings = max((a.available_balance_cents if a.available_balance_cents is not None else a.current_balance_cents or 0
                                       for a in s.scalars(select(Account).where(Account.user_id == state["user_id"],
                                           Account.type == "depository", Account.subtype == "savings"))), default=0)
                        flexible = next((r for r in state.get("results", []) if r["kind"] == "flexible"
                                         and r.get("pace") is not None and r["pace"] >= 1), None)
                        numbers = {"date": dip.date.isoformat(), "days_ahead": dip.days_ahead,
                            "shortfall_cents": dip.shortfall_cents, "buffer_cents": snap.buffer_cents,
                            "bill": "credit card minimum" if dip.cause and dip.cause.source == "card_min" else
                                    scrub_merchant(dip.cause.name) if dip.cause else "Everyday spending",
                            "low_below_zero": low < 0, "assumed_no_income": result.income == "none",
                            "savings_cover": savings >= amount, "move_cents": amount,
                            "has_budget": state.get("status") not in ("no_budget", "period_ended"),
                            "flexible_ahead": flexible is not None,
                            "starting_cents": start_balance(forecast_accounts(s, state["user_id"])) or 0,
                            "other_events": json.dumps([{"category": e.source, "cents": e.cents} for e in result.events])}
                        if flexible:
                            numbers.update(flexible_category=flexible["category"], flexible_spent_cents=flexible["spent_cents"])
                        signals.append(asdict(Signal("low_balance", low_balance_key(bill_key, dip.date), None, None, numbers)))
            return {"signals": signals, "forecast": summary}
        except Exception as exc:
            return {"signals_error": type(exc).__name__, "forecast": {"ok": False}}

    def _decide_signals(state: BudgetState) -> dict:
        actions, failures, error = {}, state.get("llm_failures", 0), state.get("signals_error")
        with session_factory() as s:
            muted = muted_stream_ids(s, state["user_id"])
        signals = [Signal(**s) for s in state.get("signals", []) if s["stream_id"] not in muted]
        for i, signal in enumerate(rank_signals(signals)):
            action, message, proposed = "alert", fallback_message(signal), None
            fallback = True
            try:
                # audit each signal once, including ones past the model-call cap
                context = gateway.build_context("cash_flow" if signal.kind == "low_balance" else "recurring", signal=signal)
                decision = decide_signal(model, context) if i < SIGNAL_CAP else None
                if decision is not None:
                    fallback = False
                    action = choose(signal, decision.action)
                    # a model's text must still name the charge and quote its code-computed prices
                    required = ([str(signal.numbers["bill"])] if signal.kind == "low_balance" else
                                [str(signal.numbers.get("merchant", "")), f"${signal.numbers['last_cents'] / 100:,.2f}"])
                    if signal.kind == "price_increase":
                        required.append(f"${signal.numbers['previous_cents'] / 100:,.2f}")
                    if action == decision.action and signal.kind != "income_change" and all(t in decision.message for t in required):
                        message = decision.message
                    if signal.kind == "low_balance" and signal.numbers.get("assumed_no_income"):
                        message = fallback_message(signal)
                elif i < SIGNAL_CAP:
                    failures += 1
                if action == "suggest_cancel":
                    with session_factory() as s:
                        period = s.get(BudgetPeriod, state["period_id"]) if state.get("period_id") else None
                        line = next((ln for ln in period.lines if ln.category == signal.category), None) if period else None
                        if line is not None and state.get("status") not in ("no_budget", "period_ended"):
                            proposed = cancel_limit(line.limit_cents, monthly_cents(signal.numbers["typical_cents"],
                                                                                  signal.numbers["frequency"]))
                    if proposed is None:
                        action, message = "alert", fallback_message(signal)
                    else:
                        message += f" If you cancel it, next month's proposed limit is ${proposed / 100:,.2f}."
            except Exception as exc:
                error = type(exc).__name__
                action, message, proposed = "alert", fallback_message(signal), None
                fallback = True
            actions[signal.key] = {"action": action, "message": message, "fallback": fallback,
                                   "proposed_limit_cents": proposed}
        return {"signals": [asdict(s) for s in signals], "signal_actions": actions,
                "signals_error": error, "llm_failures": failures}

    def decide_signals(state: BudgetState) -> dict:
        try:
            return _decide_signals(state)
        except Exception as exc:
            # ordering or other preparation can fail too; the overspend branch has already decided its alerts
            actions = {data["key"]: {"action": "alert", "message": fallback_message(Signal(**data)),
                                      "fallback": True, "proposed_limit_cents": None}
                       for data in state.get("signals", [])}
            return {"signal_actions": actions, "signals_error": type(exc).__name__}

    def diagnose(state: BudgetState) -> dict:
        facts, contexts, diagnoses, failures, stats = {}, {}, {}, state.get("llm_failures", 0), Counter()
        with session_factory() as s:
            period, txns = period_txns(s, state)
            history = load_txns(s, state["user_id"], add_months(period.start_date, -HISTORY_MONTHS),
                                period.start_date - timedelta(days=1))
            fits = {ln.category: ln.fit_json for ln in period.lines}
            lined = frozenset(ln.category for ln in period.lines)
            memory = {f["category"]: (_this_month_alerts(s, state["user_id"], period.id, f["category"]),
                                      _past_advice(s, state["user_id"], period.start_date, f["category"]))
                      for f in state["flags"]}
        for flag in state["flags"]:
            cat = flag["category"]
            f = overspend_facts(PaceResult(**flag), fits.get(cat), txns, history, period.start_date, lined=lined)
            if config.KG_MODE == "on":
                found = _graph_facts_for(session_factory, state["user_id"], f, today_of(state))
                f = replace(f, graph_facts=found if has_content(found) else None)
            facts[cat] = {**asdict(f), "proposed_limit_cents": proposed_limit_cents(f)}
            contexts[cat] = gateway.build_context("diagnose", facts=f, transactions=txns,
                                                  this_month_alerts=memory[cat][0], past_advice=memory[cat][1])
            d = invoke_structured(model, diagnosis_schema(contexts[cat]), diagnose_messages(contexts[cat]), stats=stats)
            diagnoses[cat] = d.model_dump() if d else None
            failures += d is None
        return {"facts": facts, "contexts": contexts, "diagnoses": diagnoses, "llm_failures": failures,
                "llm_calls": state.get("llm_calls", 0) + stats["calls"],
                "llm_retries": state.get("llm_retries", 0) + stats["retries"],
                "memory": {cat: alerts for cat, (alerts, _) in memory.items()}}

    def decide_action(state: BudgetState) -> dict:
        actions, failures, stats = {}, state["llm_failures"], Counter()
        for flag in state["flags"]:
            cat = flag["category"]
            d, f = state["diagnoses"][cat], state["facts"][cat]
            a = invoke_structured(model, Action, decide_messages(
                state["contexts"][cat], GraphDiagnosis(**d) if d else None, f["projected_cents"],
                f["proposed_limit_cents"], _severity(flag["reason"]), state["memory"][cat]), stats=stats)
            fallback = a is None
            if fallback:
                failures += 1
                a = Action(action="alert", message=_fallback_message(flag))
            # only a recurring cause may change next month's budget, and never the free line (it's computed) or
            # the saving line; the model's message would quote a limit it shouldn't have proposed, so the code
            # writes the alert instead
            downgraded = a.action == "adjust_next_budget" and ((d or {}).get("cause") != "recurring"
                                                               or flag.get("group") in ("free", "saving"))
            if downgraded:
                a = Action(action="alert", message=_fallback_message(flag))
            # no_op is only for an overspend the user was already told about at this severity; anything else would
            # hide it (live, the model let a pace warning stand when the category then went over its limit)
            rank = _RANK.get(_severity(flag["reason"]), 0)
            escalated = a.action == "no_op" and not any(_RANK.get(e["severity"], 0) >= rank
                                                        for e in state["memory"][cat])
            if escalated:
                a = Action(action="alert", message=_fallback_message(flag))
            # the user's spending didn't change, only where it's counted, so a cut or a new limit would be wrong.
            # and a cut they've dismissed twice isn't proposed a third time (tpc-37)
            if ((d or {}).get("cause") == "reclassified" and a.action not in ("alert", "no_op")) or (
                    a.action == "suggest_cuts" and cut_was_dismissed(f.get("graph_facts"))):
                a = Action(action="alert", message=_fallback_message(flag))
            actions[cat] = {"action": a.action, "message": a.message, "fallback": fallback, "downgraded": downgraded,
                            "escalated": escalated,
                            "proposed_limit_cents": f["proposed_limit_cents"] if a.action == "adjust_next_budget" else None}
        return {"actions": actions, "llm_failures": failures, "llm_calls": state.get("llm_calls", 0) + stats["calls"],
                "llm_retries": state.get("llm_retries", 0) + stats["retries"]}

    def log_advice(state: BudgetState) -> dict:
        ids, adjust = [], []
        with session_factory() as s:
            for flag in state.get("flags", []):
                cat, act, d = flag["category"], state["actions"][flag["category"]], state["diagnoses"][flag["category"]]
                if act["action"] == "no_op":
                    continue
                alert = Alert(user_id=state["user_id"], period_id=state["period_id"], kind="overspend",
                              category=cat, severity=_severity(flag["reason"]),
                              cause=d["cause"] if d else None, action=act["action"], message=act["message"],
                              proposed_limit_cents=act["proposed_limit_cents"], status="pending_user")
                s.add(alert)
                s.flush()       # the advice row needs the alert's id
                s.add(AdviceLog(user_id=state["user_id"], run_id=state["run_id"], alert_id=alert.id, category=cat,
                                advice_text=act["message"]))
                ids.append(alert.id)
                if act["action"] == "adjust_next_budget":
                    adjust.append(alert.id)
            for data in state.get("signals", []):
                signal = Signal(**data)
                # the alert key, not a budget category, identifies a signal. recheck at the write boundary
                if signal.kind != "low_balance" and s.scalar(select(Alert.id).where(Alert.user_id == state["user_id"], Alert.signal_key == signal.key)):
                    continue
                act = state["signal_actions"][signal.key]
                alert = Alert(user_id=state["user_id"], period_id=state.get("period_id"), kind=signal.kind,
                              stream_id=signal.stream_id, signal_key=signal.key, category=signal.category,
                              severity="high" if signal.numbers.get("low_below_zero") else "medium",
                              signal_cents=signal.numbers.get("shortfall_cents"), signal_days=signal.numbers.get("days_ahead"),
                              action=act["action"], message=act["message"],
                              proposed_limit_cents=act["proposed_limit_cents"], status="pending_user")
                s.add(alert)
                s.flush()
                s.add(AdviceLog(user_id=state["user_id"], run_id=state["run_id"], alert_id=alert.id,
                                category=signal.category, advice_text=act["message"]))
                ids.append(alert.id)
            # save the summary before forgetting prompt facts; resume needs only ids and decisions
            s.get(AgentRun, state["run_id"]).summary_json = summarize({**state, "alert_ids": ids})
            # now, not on the next run: a decision has to find the pending belief already there
            project.safely(s, "log_advice", lambda: project.project_advice(
                s, state["user_id"], {state["run_id"]: today_of(state)}))
            s.commit()
        cleared = {"signals": [], "signal_actions": {}}
        if checkpointer is not None:
            # the cli's report lives in process memory, never in graph state saved by the checkpointer
            if report is not None:
                report.update({k: state.get(k, [] if k in ("results", "flags") else {})
                               for k in ("results", "flags", "facts", "contexts", "memory", "diagnoses", "actions")})
            cleared.update(results=[], flags=[], facts={}, contexts={}, memory={}, diagnoses={},
                           actions={c: {k: v for k, v in a.items() if k in ("action", "fallback", "downgraded", "escalated")}
                                    for c, a in state.get("actions", {}).items()})
        return {"alert_ids": ids, "adjust_alert_ids": adjust, **cleared}

    # architecture 6.6: a proposal that changes the budget waits for the user. the alert rows are the source of
    # truth: a decision recorded while no resume reached the run (the resume failed, or the click landed before
    # the pause) still counts. each resume carries one [alert_id, decision] pair (not a dict: langgraph reads a
    # dict resume as interrupt id -> value); decisions can arrive in any order, and the run finishes once all are in
    def await_approval(state: BudgetState) -> dict:
        ids = state["adjust_alert_ids"]
        decisions = dict(state.get("decisions") or {})
        while True:
            decisions.update(_decided(session_factory, ids))
            pending = sorted(set(ids) - set(decisions))
            if not pending:
                return {"decisions": decisions}
            alert_id, decision = interrupt({"waiting_for": pending})
            decisions.setdefault(int(alert_id), decision)

    def summarize(state: BudgetState) -> dict:
        flags = state.get("flags", [])
        return {
            "period_id": state.get("period_id"),
            "flags": [{k: f[k] for k in ("category", "reason", "pace", "spent_cents", "limit_cents")} for f in flags],
            "causes": {c: (d or {}).get("cause") for c, d in state.get("diagnoses", {}).items()},
            "actions": {c: a["action"] for c, a in state.get("actions", {}).items()},
            "alert_ids": state.get("alert_ids", []),
            "unbudgeted": [r["category"] for r in state.get("results", []) if r["kind"] == "unbudgeted"],
            "uncategorized": state.get("uncategorized", 0),
            "sync": state.get("sync"),
            "llm_failures": state.get("llm_failures", 0),
            "llm_calls": state.get("llm_calls", 0),
            "llm_retries": state.get("llm_retries", 0),
            **({"graph_error": True} if state.get("graph_error") else {}),
            "decisions": {str(k): v for k, v in (state.get("decisions") or {}).items()},
            "as_of": state["today"],
            "streams": state.get("streams", {"count": 0, "new": [], "price_up": [], "income_change": []}),
            "signals_error": state.get("signals_error"),
            "forecast": state.get("forecast"),
        }

    def persist_state(state: BudgetState) -> dict:
        status = state["status"] if state.get("status") in ("no_budget", "period_ended") else "ok"
        with session_factory() as s:
            run = s.get(AgentRun, state["run_id"])
            summary = {**(run.summary_json or {}),
                       "decisions": {str(k): v for k, v in (state.get("decisions") or {}).items()}}
            run.status, run.finished_at, run.summary_json = status, utcnow(), summary
            s.commit()
        return {"status": status}

    def after_load(state: BudgetState) -> str:
        return "detect_recurring" if state.get("status") in ("no_budget", "period_ended") else "categorize"

    def after_recurring(state: BudgetState) -> str:
        return "decide_signals" if state.get("status") in ("no_budget", "period_ended") else "detect_overspend"

    def after_detect(state: BudgetState) -> str:
        return "diagnose" if state["flags"] else "decide_signals"

    # without a checkpointer there is nowhere to pause (the cli before tpc-20, tests), so nothing waits
    def after_advice(state: BudgetState) -> str:
        return "await_approval" if checkpointer is not None and state.get("adjust_alert_ids") else "persist_state"

    graph = StateGraph(BudgetState)
    for name, fn in (("load_state", load_state), ("sync", sync), ("categorize", categorize),
                     ("update_totals", update_totals), ("detect_overspend", detect_overspend),
                     ("detect_recurring", detect_recurring), ("update_graph", update_graph), ("decide_signals", decide_signals),
                     ("forecast_cash", forecast_cash),
                     ("diagnose", diagnose), ("decide_action", decide_action), ("log_advice", log_advice),
                     ("await_approval", await_approval), ("persist_state", persist_state)):
        graph.add_node(name, fn)
    # sync first: a user with no budget yet gets it fit on fresh data, not on whatever was stored before
    graph.add_edge(START, "sync")
    graph.add_edge("sync", "load_state")
    graph.add_conditional_edges("load_state", after_load, ["categorize", "detect_recurring"])
    graph.add_edge("categorize", "update_totals")
    graph.add_edge("update_totals", "detect_recurring")
    graph.add_edge("detect_recurring", "update_graph")
    graph.add_edge("update_graph", "forecast_cash")
    graph.add_conditional_edges("forecast_cash", after_recurring, ["detect_overspend", "decide_signals"])
    graph.add_conditional_edges("detect_overspend", after_detect, ["diagnose", "decide_signals"])
    graph.add_edge("diagnose", "decide_action")
    graph.add_edge("decide_action", "decide_signals")
    graph.add_edge("decide_signals", "log_advice")
    graph.add_conditional_edges("log_advice", after_advice, ["await_approval", "persist_state"])
    graph.add_edge("await_approval", "persist_state")
    graph.add_edge("persist_state", END)
    return graph.compile(checkpointer=BudgetCheckpoints(checkpointer) if checkpointer is not None else None)


# the decided ones among these alerts, from their rows
def _decided(session_factory, alert_ids) -> dict[int, str]:
    with session_factory() as s:
        rows = s.scalars(select(Alert).where(Alert.id.in_(list(alert_ids)),
                                             Alert.status.in_(("accepted", "dismissed")))).all()
        return {a.id: a.status for a in rows}


# one resume at a time per run (in-process, one api worker): two decisions for one paused thread would otherwise
# continue the same checkpoint at once, and one would be lost
_resume_locks: dict[int, threading.Lock] = {}
_resume_locks_guard = threading.Lock()


def _resume_lock(run_id: int) -> threading.Lock:
    with _resume_locks_guard:
        return _resume_locks.setdefault(run_id, threading.Lock())


def _thread(run_id: int) -> dict:
    return {"configurable": {"thread_id": f"budget-{run_id}"}}


def _interrupted(result: dict) -> Optional[list[int]]:
    found = result.get("__interrupt__")
    return sorted(i for it in found for i in it.value["waiting_for"]) if found else None


def _mark(session_factory, run_id: int, status: str, summary: Optional[dict] = None, finished: bool = True) -> None:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        row.status = status
        if finished:
            row.finished_at = utcnow()
        if summary is not None:
            row.summary_json = summary
        s.commit()


# the alert ids a paused run is waiting on; [] when it isn't paused or its checkpoint is gone
def waiting_for(session_factory, checkpointer: BaseCheckpointSaver, run_id: int) -> list[int]:
    graph = build_budget_graph(session_factory, model=None, gateway=None, checkpointer=checkpointer)
    snapshot = graph.get_state(_thread(run_id))
    return sorted(i for task in snapshot.tasks for it in task.interrupts for i in it.value["waiting_for"])


# one run for one user and date. the agent_runs row is written before the graph starts, so a crash in any
# node still leaves a run marked error (then re-raised)
def run_budget(session_factory, user_id: str, today: date, *, model: Optional[BaseChatModel] = None,
               sync_fn: Optional[SyncFn] = None, audit_path=None,
               checkpointer: Optional[BaseCheckpointSaver] = None,
               on_start: Optional[Callable[[int], None]] = None) -> dict:
    model = model or get_chat_model()
    with session_factory() as s:
        run = AgentRun(user_id=user_id, graph="budget", status="running")
        s.add(run)
        s.commit()
        run_id = run.id
    thread = _thread(run_id)["configurable"]["thread_id"]
    report = {}
    try:
        if checkpointer is not None:
            # sqlite reuses the id of a deleted run (seed --replace), and a thread left from that run would hand
            # its decisions to this one, skipping the pause. a new run id never has history of its own
            checkpointer.delete_thread(thread)
        if on_start is not None:
            on_start(run_id)        # the api answers with the id while the run goes on in the background
        with session_factory() as gs:
            gateway = PrivacyGateway(gs, user_id, audit_path=audit_path)    # one pseudonym per run
            graph = build_budget_graph(session_factory, model=model, gateway=gateway, sync_fn=sync_fn,
                                       checkpointer=checkpointer, report=report)
            result = graph.invoke({"user_id": user_id, "today": today.isoformat(), "run_id": run_id,
                                   "status": "running", "llm_failures": 0},
                                  **({"config": _thread(run_id), "durability": "exit"} if checkpointer is not None else {}))
            result.update(report)
    except Exception as exc:
        _mark(session_factory, run_id, "error", {"error": type(exc).__name__})
        if checkpointer is not None:
            try:
                checkpointer.delete_thread(thread)
            except Exception as cleanup:
                logger.warning("budget checkpoint cleanup failed: %s", type(cleanup).__name__)
        raise
    waiting = _interrupted(result)
    if waiting is None:
        # only a paused run needs its checkpoint; a finished one would keep its unscrubbed facts forever
        if checkpointer is not None:
            checkpointer.delete_thread(thread)
        return {**result, "waiting_for": []}
    _mark(session_factory, run_id, "awaiting_user", finished=False)
    # a decision made after the run read its alerts but before this mark found no awaiting run to resume
    decided = _decided(session_factory, waiting)
    if decided:
        alert_id = min(decided)
        try:
            return {**result, **resume_budget_run(session_factory, run_id, alert_id, decided[alert_id],
                                                  checkpointer=checkpointer)}
        except Exception:
            logger.warning("budget run %s: resume of an early decision failed", run_id, exc_info=True)
    return {**result, "status": "awaiting_user", "waiting_for": waiting}


# continue a paused run with one decision. it pauses again while other proposals are still undecided. no model:
# nothing after the pause calls one, so a broken model setting can't block a decision
def resume_budget_run(session_factory, run_id: int, alert_id: int, decision: str, *,
                      checkpointer: BaseCheckpointSaver) -> dict:
    with _resume_lock(run_id):
        # another resume may have finished the run (or taken this decision) while this one waited for the lock
        if alert_id not in waiting_for(session_factory, checkpointer, run_id):
            with session_factory() as s:
                status = s.get(AgentRun, run_id).status
            return {"run_id": run_id, "status": status,
                    "waiting_for": waiting_for(session_factory, checkpointer, run_id)}
        graph = build_budget_graph(session_factory, model=None, gateway=None, checkpointer=checkpointer)
        try:
            result = graph.invoke(Command(resume=[alert_id, decision]), _thread(run_id), durability="exit")
        except Exception as exc:
            # still paused means nothing ran: the run keeps waiting (its other proposals too) and can be resumed
            # again. only a run that got past the pause and then failed is an error
            if not waiting_for(session_factory, checkpointer, run_id):
                _mark(session_factory, run_id, "error", {"error": type(exc).__name__})
            raise
        waiting = _interrupted(result)
        if waiting is not None:
            return {**result, "status": "awaiting_user", "waiting_for": waiting}
        checkpointer.delete_thread(_thread(run_id)["configurable"]["thread_id"])
        return {**result, "waiting_for": []}


def _money(cents: Optional[int]) -> str:
    return "-" if cents is None else f"${cents / 100:,.2f}"


# a failed sync means the budget was checked on stale data; say so instead of a bare "ok"
def _sync_line(sync: Optional[dict]) -> str:
    if not sync or sync.get("skipped"):
        return "skipped"
    line = f"{sync['items']} item(s)"
    return f"{line}, errors: {', '.join(sync['errors'])} (data may be stale)" if sync["errors"] else line


def _print_report(state: dict) -> None:
    status = state["status"]
    if status == "awaiting_user":
        status += f" (waiting for alert(s) {', '.join(str(i) for i in state['waiting_for'])})"
    print(f"run {state['run_id']}  status: {status}  period: {state.get('period_id')}")
    for r in state.get("results", []):
        pace = "-" if r["pace"] is None else f"{r['pace']:.2f}"
        mark = f"  FLAG ({r['reason']})" if r["flagged"] else ""
        print(f"  {r['category']:<22} {r['kind']:<10} {_money(r['spent_cents']):>11} of "
              f"{_money(r['limit_cents']):>11}  pace {pace}{mark}")
    for flag in state.get("flags", []):
        cat = flag["category"]
        d, a = state["diagnoses"][cat], state["actions"][cat]
        print(f"\n{cat} ({flag['reason']})")
        print(f"  cause:  {d['cause']} - {d['evidence']}" if d else "  cause:  unknown (llm failed)")
        note = " (fallback)" if a["fallback"] else " (downgraded: only recurring adjusts)" if a.get("downgraded") else ""
        print(f"  action: {a['action']}{note}")
        print(f"  message: {a['message']}")
        if a["proposed_limit_cents"] is not None:
            print(f"  proposed limit: {_money(a['proposed_limit_cents'])}")
    unbudgeted = [r["category"] for r in state.get("results", []) if r["kind"] == "unbudgeted"]
    print(f"\nsync: {_sync_line(state.get('sync'))}")
    print(f"alerts written: {len(state.get('alert_ids', []))}")
    print(f"unbudgeted: {', '.join(unbudgeted) or 'none'}")
    print(f"llm failures: {state.get('llm_failures', 0)}")


# python -m app.agents.budget_graph --user <id> [--date YYYY-MM-DD] [--no-sync] [--model NAME], from backend/
def main(argv=None, session_factory=None, model: Optional[BaseChatModel] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the budget agent once for one user.")
    parser.add_argument("--user", required=True)
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD, default today")
    parser.add_argument("--no-sync", action="store_true", help="skip the plaid sync (synthetic users)")
    parser.add_argument("--model", help="ollama model, default OLLAMA_MODEL")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    config.BUDGET_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(config.BUDGET_CHECKPOINT_PATH), check_same_thread=False))
    from app.budget_service.daily import user_lock
    lock = user_lock(args.user)
    if not lock.acquire(blocking=False, session_factory=session_factory):
        print("A budget or goal run is already in progress.")
        return 1
    try:
        state = run_budget(session_factory, args.user, args.date, model=model or get_chat_model(model=args.model),
                           sync_fn=None if args.no_sync else plaid_sync, checkpointer=saver)
    except Exception as exc:
        print(f"run failed: {type(exc).__name__}")
        return 1
    finally:
        lock.release()
    _print_report(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# Recover abandoned runs at startup while preserving ownership held by other processes.
def close_interrupted_runs(session_factory) -> int:
    from app.db.models import UserRunClaim
    with session_factory() as s:
        stuck = s.scalars(select(AgentRun).where(AgentRun.graph.in_(("budget", "simulate", "goals")),
                                                 AgentRun.status == "running",
                                                 ~select(UserRunClaim.user_id).where(
                                                     UserRunClaim.user_id == AgentRun.user_id,
                                                     UserRunClaim.expires_at > utcnow()).exists())).all()
        for row in stuck:
            row.status, row.finished_at, row.summary_json = "error", utcnow(), {"error": "Interrupted"}
        s.commit()
        return len(stuck)
