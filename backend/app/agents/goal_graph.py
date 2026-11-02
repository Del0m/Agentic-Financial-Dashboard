import argparse
import json
import logging
import sys
from datetime import date
from decimal import Decimal
from typing import Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.budget_llm import invoke_structured
from app.agents.goal_llm import GoalProposal, goal_messages
from app.agents.llm import get_chat_model
from app.budget_engine.fit import HISTORY_MONTHS
from app.budget_engine.goals import (GoalFacts, GoalPlan, build_goal, card_minimums, emergency_gap, monthly_surplus,
                                     offered_kinds)
from app.budget_engine.groups import NEEDS_DEFAULT
from app.budget_engine.txns import add_months, month_start, round_cents
from app.budget_service.daily import running_elsewhere, sim_today, user_lock
from app.budget_service.debts import load_debts
from app.budget_service.goal_prefs import set_goal_prefs, user_goal_prefs
from app.budget_service.initialize import load_txns
from app.db.models import Account, AgentRun, BudgetLine, BudgetPeriod, Goal, User, utcnow
from app.privacy.gateway import PrivacyGateway

logger = logging.getLogger(__name__)

# the goal agent (architecture section 4, spec: tpc-23). code measures what's left each month, the llm picks goals
# within it, and code works out each goal's target and date. nothing pauses until approval arrives with tpc-25, so
# there's no checkpointer. tpc-24's affordability check and retry goes between propose and build


class GoalState(TypedDict, total=False):
    user_id: str
    today: str
    status: str            # ok | no_surplus | nothing_offered | error
    reason: Optional[str]
    surplus_cents: Optional[int]
    card_minimums_cents: int
    cap_cents: Optional[int]
    gap_cents: int
    offered: list[str]
    start: str
    context: str
    proposal: list
    plans: list[GoalPlan]
    dropped: list[dict]
    goal_ids: list[int]
    withdrawn: int
    run_id: int


# the categories in the open budget's needs lines, or the default ones for a user with no budget yet
def _needs(session, user_id: str) -> frozenset[str]:
    period = session.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user_id, BudgetPeriod.status == "open")
                             .order_by(BudgetPeriod.start_date.desc())).first()
    if period is None:
        return NEEDS_DEFAULT
    return frozenset(ln.category for ln in session.scalars(
        select(BudgetLine).where(BudgetLine.period_id == period.id, BudgetLine.group == "needs")))


def _savings_balance(session, user_id: str) -> int:
    return sum(a.current_balance_cents or 0 for a in session.scalars(
        select(Account).where(Account.user_id == user_id, Account.type == "depository",
                              Account.subtype == "savings")))


def _supersede(session, user_id: str) -> int:
    old = session.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status == "proposed")).all()
    for goal in old:
        goal.status = "superseded"
    return len(old)


def build_goal_graph(session_factory, *, model: BaseChatModel, gateway: PrivacyGateway):
    def load_facts(state: GoalState) -> dict:
        user_id, today = state["user_id"], date.fromisoformat(state["today"])
        start = add_months(month_start(today), 1)
        with session_factory() as s:
            # a month further back than it measures, so the window's edge doesn't look like the start of the history
            txns = load_txns(s, user_id, add_months(month_start(today), -HISTORY_MONTHS - 1), today)
            debts = load_debts(s, user_id)
            minimums = card_minimums(debts)
            measured = monthly_surplus(txns, today)
            surplus = None if measured is None else measured - minimums
            if surplus is None or surplus <= 0:
                return {"status": "no_surplus", "reason": "no_surplus", "surplus_cents": surplus,
                        "card_minimums_cents": minimums}
            prefs = user_goal_prefs(s, user_id)
            gap = emergency_gap(txns, today, _needs(s, user_id), _savings_balance(s, user_id))
        offered = offered_kinds(debts, gap, prefs.skip_kinds, start=start)
        cap = round_cents(Decimal(surplus) * Decimal(str(prefs.max_share)))
        facts = {"surplus_cents": surplus, "card_minimums_cents": minimums, "cap_cents": cap, "gap_cents": gap,
                 "offered": offered,
                 "start": start.isoformat()}
        if not offered:
            return {**facts, "status": "nothing_offered", "reason": "nothing_offered"}
        context = gateway.build_context("goals", goal_facts=GoalFacts(
            surplus, cap, tuple(offered), gap, start, prefs.protected_categories, prefs.note))
        return {**facts, "context": context}

    def propose(state: GoalState) -> dict:
        proposal = invoke_structured(model, GoalProposal, goal_messages(state["context"]), raise_unreachable=True)
        if proposal is None:
            return {"status": "error", "reason": "bad_model_output"}
        return {"proposal": proposal.goals}

    def build(state: GoalState) -> dict:
        with session_factory() as s:
            debts = load_debts(s, state["user_id"])
        plans, dropped, seen = [], [], set()
        for goal in state["proposal"]:
            reason = "not_offered" if goal.kind not in state["offered"] else "repeat" if goal.kind in seen else None
            if reason:
                dropped.append({"kind": goal.kind, "reason": reason})
                continue
            seen.add(goal.kind)
            monthly_cents = round_cents(Decimal(str(goal.monthly_dollars)) * 100)
            # the prompt's default: a small model sometimes leaves it out, and that shouldn't cost the top goal
            strategy = goal.strategy or "avalanche"
            plans.append(build_goal(goal.kind, len(plans) + 1, monthly_cents, strategy, goal.rationale,
                                    debts=debts, gap_cents=state["gap_cents"],
                                    start=date.fromisoformat(state["start"])))
        if not plans:
            return {"status": "error", "reason": "no_usable_goals", "dropped": dropped}
        return {"plans": plans, "dropped": dropped}

    # one commit: the user only ever sees one current proposal, and a failed run leaves the last one standing
    def persist(state: GoalState) -> dict:
        with session_factory() as s:
            _supersede(s, state["user_id"])
            rows = [Goal(user_id=state["user_id"], run_id=state["run_id"], kind=p.kind, priority=p.priority,
                         monthly_cents=p.monthly_cents, target_cents=p.target_cents, target_date=p.target_date,
                         strategy=p.strategy, rationale=p.rationale) for p in state["plans"]]
            s.add_all(rows)
            s.commit()
            return {"status": "ok", "goal_ids": [r.id for r in rows]}

    # a finished run with nothing to propose: the last proposal no longer fits. a failed run says nothing about that
    def withdraw(state: GoalState) -> dict:
        with session_factory() as s:
            withdrawn = _supersede(s, state["user_id"])
            s.commit()
        return {"withdrawn": withdrawn}

    graph = StateGraph(GoalState)
    graph.add_node("load_facts", load_facts)
    graph.add_node("propose", propose)
    graph.add_node("build", build)
    graph.add_node("persist", persist)
    graph.add_node("withdraw", withdraw)
    graph.add_edge(START, "load_facts")
    stopped = lambda state: END if state.get("status") else None
    graph.add_conditional_edges("load_facts", lambda st: "withdraw" if st.get("status") else "propose")
    graph.add_conditional_edges("propose", lambda st: stopped(st) or "build")
    graph.add_conditional_edges("build", lambda st: stopped(st) or "persist")
    graph.add_edge("persist", END)
    graph.add_edge("withdraw", END)
    return graph.compile()


def _finish(session_factory, run_id: int, result: dict) -> None:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        row.status = "error" if result["status"] == "error" else "ok"
        row.finished_at = utcnow()
        row.summary_json = {"outcome": result["status"], "reason": result.get("reason"),
                            "surplus_cents": result.get("surplus_cents"), "cap_cents": result.get("cap_cents"),
                            "card_minimums_cents": result.get("card_minimums_cents", 0),
                            "gap_cents": result.get("gap_cents"), "goal_ids": result.get("goal_ids", []),
                            "withdrawn": result.get("withdrawn", 0),
                            "dropped": result.get("dropped", [])}
        s.commit()


# one goal run at a time for a user, and not during a budget run: they read the same money. a busy user writes nothing.
# the lock covers this process and the run rows cover the rest, since the cli is a separate one
def run_goals(session_factory, user_id: str, today: date, *, model: Optional[BaseChatModel] = None,
              audit_path=None) -> dict:
    lock = user_lock(user_id)
    if not lock.acquire(blocking=False):
        return {"status": "busy", "goal_ids": [], "dropped": []}
    try:
        if running_elsewhere(session_factory, user_id):
            return {"status": "busy", "goal_ids": [], "dropped": []}
        model = model or get_chat_model()
        with session_factory() as s:
            run = AgentRun(user_id=user_id, graph="goals", status="running")
            s.add(run)
            s.commit()
            run_id = run.id
        try:
            with session_factory() as gs:
                gateway = PrivacyGateway(gs, user_id, audit_path=audit_path)    # one pseudonym per run
                graph = build_goal_graph(session_factory, model=model, gateway=gateway)
                result = graph.invoke({"user_id": user_id, "today": today.isoformat(), "run_id": run_id})
        except KeyboardInterrupt:
            _finish(session_factory, run_id, {"status": "error", "reason": "Interrupted"})
            raise
        except Exception as exc:
            # an unreachable model, say: the run says so and the last proposal stands
            logger.warning("goal run failed: %s", type(exc).__name__, exc_info=True)
            result = {"status": "error", "reason": type(exc).__name__}
        result = {"goal_ids": [], "dropped": [], **result, "run_id": run_id}
        _finish(session_factory, run_id, result)
        return result
    finally:
        lock.release()


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _print_report(state: dict) -> None:
    status = state["status"]
    if status == "busy":
        print("A budget or goal run is already in progress for this user.")
        return
    if status == "error":
        print(f"run failed: {state.get('reason')}")
        return
    surplus = state.get("surplus_cents")
    withdrawn = "The earlier proposal no longer fits, so it's withdrawn." if state.get("withdrawn") else None
    if status == "no_surplus":
        print("No goals: no complete month to measure yet." if surplus is None
              else f"No goals: {_dollars(surplus)} left in a typical month.")
        if withdrawn:
            print(withdrawn)
        return
    minimums = state.get("card_minimums_cents")
    after = f" after {_dollars(minimums)} of card minimums" if minimums else ""
    print(f"surplus {_dollars(surplus)} a month{after}; goals may use {_dollars(state['cap_cents'])}")
    if status == "nothing_offered":
        print("No goals: the preferences skip every kind that's available.")
        if withdrawn:
            print(withdrawn)
        return
    total = 0
    for p in state["plans"]:
        total += p.monthly_cents
        when = p.target_date.strftime("%Y-%m") if p.target_date else "no end date"
        strategy = f" ({p.strategy})" if p.strategy else ""
        # tpc-24 will refuse these; until then the report says so
        over = " (over cap)" if total > state["cap_cents"] else ""
        print(f"{p.priority}. {p.kind}{strategy}: {_dollars(p.monthly_cents)}/mo, target "
              f"{_dollars(p.target_cents)}, {when}{over}")
        print(f"   {p.rationale}")
    for d in state.get("dropped", []):
        print(f"dropped {d['kind']}: {d['reason']}")


def main(argv=None, session_factory=None, model: Optional[BaseChatModel] = None) -> int:
    parser = argparse.ArgumentParser(description="Propose goals for one user.")
    parser.add_argument("--user", required=True)
    parser.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD, default today (a simulated user's date)")
    parser.add_argument("--model", help="ollama model, default OLLAMA_MODEL")
    parser.add_argument("--prefs", help="goal preferences as json: skip_kinds, max_share, protected_categories, note")
    args = parser.parse_args(argv)
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    with session_factory() as s:
        if s.get(User, args.user) is None:
            print(f"No user {args.user}.")
            return 1
        if args.prefs:
            try:
                set_goal_prefs(s, args.user, json.loads(args.prefs))
            except (ValueError, TypeError) as exc:
                print(f"bad --prefs: {exc}")
                return 1
            s.commit()
        today = args.date or sim_today(s, args.user) or date.today()
    state = run_goals(session_factory, args.user, today, model=model or get_chat_model(model=args.model))
    _print_report(state)
    return 1 if state["status"] in ("error", "busy") else 0


if __name__ == "__main__":
    sys.exit(main())
