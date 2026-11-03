import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Callable, Optional, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select

from app.agents.budget_graph import _interrupted, _resume_lock
from app.agents.budget_llm import UNREACHABLE, invoke_structured
from app.agents.goal_llm import GoalProposal, goal_messages
from app.agents.llm import get_chat_model
from app.budget_engine.fit import HISTORY_MONTHS
from app.budget_engine.goals import (MIN_MONTHLY_CENTS, GoalAllocation, GoalFacts, GoalIssue, GoalPlan, build_goal,
                                     card_minimums, committed_cents, emergency_gap, goal_cap, monthly_surplus, normalize_goal,
                                     offered_kinds, validate_allocations, validate_plans)
from app.budget_engine.payoff import Debt
from app.budget_engine.groups import NEEDS_DEFAULT
from app.budget_engine.txns import add_months, month_start, round_cents
from app.budget_service.daily import running_elsewhere, sim_today, user_lock
from app.budget_service.debts import load_debts, incomplete_card_obligations
from app.budget_service.goal_lines import committed_goals, expire_paused_runs
from app.budget_service.goal_prefs import set_goal_prefs, user_goal_prefs
from app.budget_service.forecast_service import snapshot
from app.budget_service.initialize import load_txns, history_start
from app.db.models import Account, AgentRun, BudgetLine, BudgetPeriod, Goal, User, utcnow
from app.privacy.gateway import PrivacyGateway

logger = logging.getLogger(__name__)

# the goal agent (architecture section 4, spec: tpc-23). code measures what's left each month, the llm picks goals
# within it, and code works out each goal's target and date. tpc-24 validates the whole candidate before anything is
# persisted, and with a checkpointer the run then waits for the user's decisions (tpc-25). without one (the cli) nothing
# waits, and its goals can still be decided in the app

MAX_GOAL_CALLS = 2
USE_FORECAST = True


class GoalState(TypedDict, total=False):
    user_id: str
    today: str
    status: str            # ok | no_surplus | nothing_offered | no_affordable_goals | error
    reason: Optional[str]
    surplus_cents: Optional[int]
    card_minimums_cents: int
    cap_cents: Optional[int]
    committed_cents: int        # what accepted goals already take each month
    committed_ids: list[int]    # the accepted goals this run planned around; persist checks they're unchanged
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
    debts: list[Debt]
    allocations: list[GoalAllocation]
    attempts: int
    validation_issues: list[dict]
    validation_history: list[dict]
    decisions: dict             # goal id -> the status its row ended on
    waiting_for: list[int]
    surplus_source: str
    surplus_fallback_reason: Optional[str]
    forecast_surplus_cents: Optional[int]
    measured_surplus_cents: Optional[int]
    forecast_as_of: str


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


# returns how many goals it superseded, and the paused runs that leaves with nothing to wait for
def _supersede(session, user_id: str, run_id: int) -> tuple[int, list[int]]:
    old = session.scalars(select(Goal).where(Goal.user_id == user_id, Goal.status == "proposed")).all()
    for goal in old:
        goal.status = "superseded"
    return len(old), expire_paused_runs(session, old, keep=run_id)


def _thread_id(run_id: int) -> str:
    return f"goals-{run_id}"


def _thread(run_id: int) -> dict:
    return {"configurable": {"thread_id": _thread_id(run_id)}}


# a checkpoint that won't delete is logged and left: it mustn't stop the run's rows from saying how the run ended
def _delete_threads(checkpointer: Optional[BaseCheckpointSaver], run_ids: list[int]) -> None:
    if checkpointer is None:
        return
    for run_id in run_ids:
        try:
            checkpointer.delete_thread(_thread_id(run_id))
        except Exception as exc:
            logger.warning("couldn't delete checkpoint %s: %s", _thread_id(run_id), type(exc).__name__)


def build_goal_graph(session_factory, *, model: Optional[BaseChatModel], gateway: Optional[PrivacyGateway],
                     progress=None, claim=None, checkpointer: Optional[BaseCheckpointSaver] = None):
    progress = progress if progress is not None else {}
    def load_facts(state: GoalState) -> dict:
        user_id, today = state["user_id"], date.fromisoformat(state["today"])
        start = add_months(month_start(today), 1)
        with session_factory() as s:
            # a month further back than it measures, so the window's edge doesn't look like the start of the history
            txns = load_txns(s, user_id, add_months(month_start(today), -HISTORY_MONTHS - 1), today)
            if incomplete_card_obligations(s, user_id):
                return {"status": "error", "reason": "incomplete_card_obligations"}
            debts = load_debts(s, user_id)
            minimums = card_minimums(debts)
            boundary = history_start(s, user_id, today)
            measured = monthly_surplus(txns, today, history_start=boundary)
            measured = None if measured is None else measured - minimums
            snap = snapshot(s, user_id, today) if USE_FORECAST else None
            source = "forecast" if snap is not None and snap.result.surplus_fallback is None else "measured"
            surplus = snap.result.monthly_surplus_cents if source == "forecast" else measured
            basis = {"surplus_source": source, "surplus_fallback_reason": snap.result.surplus_fallback if snap else None,
                     "forecast_surplus_cents": snap.result.monthly_surplus_cents if snap else None,
                     "measured_surplus_cents": measured, "forecast_as_of": today.isoformat()}
            if surplus is None or surplus <= 0:
                return {**basis, "status": "no_surplus", "reason": "no_surplus", "surplus_cents": surplus,
                        "card_minimums_cents": minimums}
            prefs = user_goal_prefs(s, user_id)
            committed = committed_goals(s, user_id)
            gap = emergency_gap(txns, today, _needs(s, user_id), _savings_balance(s, user_id), history_start=boundary)
        # a kind with an accepted goal isn't offered again, and what accepted goals take comes off the cap. they can
        # outgrow it (the surplus fell), and then there's nothing left to offer
        offered = offered_kinds(debts, gap, (*prefs.skip_kinds, *(c.kind for c in committed)), start=start)
        taken = committed_cents(committed)
        cap = goal_cap(surplus, taken, prefs.max_share, net_of_goals=source == "forecast")
        facts = {**basis, "surplus_cents": surplus, "card_minimums_cents": minimums, "cap_cents": cap, "gap_cents": gap,
                 "offered": offered, "committed_cents": taken, "committed_ids": [c.id for c in committed],
                 "start": start.isoformat(), "debts": debts}
        if not offered:
            return {**facts, "status": "nothing_offered", "reason": "nothing_offered"}
        if cap < MIN_MONTHLY_CENTS:
            return {**facts, "status": "no_affordable_goals", "reason": "cap_below_minimum"}
        context = gateway.build_context("goals", goal_facts=GoalFacts(
            surplus, cap, tuple(offered), gap, start, prefs.protected_categories, prefs.note, taken, source),
            goal_debts=debts)
        return {**facts, "context": context}

    def propose(state: GoalState) -> dict:
        attempt = state.get('attempts', 0) + 1
        progress['attempts'] = attempt
        try:
            proposal = invoke_structured(model, GoalProposal, goal_messages(state['context']),
                                         retries=0, raise_unreachable=True)
        except UNREACHABLE as exc:
            logger.warning('goal model unreachable: %s', type(exc).__name__)
            return {'status': 'error', 'reason': type(exc).__name__, 'attempts': attempt, 'plans': []}
        return {'proposal': None if proposal is None else proposal.goals, 'attempts': attempt}

    def validate_candidate(state: GoalState) -> dict:
        start = date.fromisoformat(state['start'])
        allocations, plans = [], []
        if state['proposal'] is None:
            issues = [GoalIssue('bad_model_output', 'Return valid JSON with one to three goals: offered kind, '
                                'monthly_dollars from $1 to $1,000,000, optional avalanche/snowball strategy, '
                                'and a nonempty rationale.')]
        else:
            allocations = [normalize_goal(g.kind, g.monthly_dollars, g.strategy, g.rationale)
                           for g in state['proposal']]
            issues = validate_allocations(allocations, cap_cents=state['cap_cents'], offered=state['offered'])
            unsafe = {i.index for i in issues if i.code in ('not_offered', 'repeat', 'bad_amount')}
            for index, g in enumerate(allocations, 1):
                if index not in unsafe:
                    plans.append(build_goal(g.kind, index, g.monthly_cents, g.strategy, g.rationale,
                                            debts=state['debts'], gap_cents=state['gap_cents'], start=start))
            issues.extend(validate_plans(plans, start=start))
        failures = [asdict(i) for i in issues]
        history = [*state.get('validation_history', []), {'attempt': state['attempts'], 'issues': failures}]
        progress.update(validation_history=history, validation_issues=failures)
        update = {'allocations': allocations, 'plans': [] if issues else plans,
                  'validation_issues': failures, 'validation_history': history}
        if issues and state['attempts'] >= MAX_GOAL_CALLS:
            update.update(status='error', reason='bad_model_output' if state['proposal'] is None
                          else 'validation_failed')
        return update

    def correction(state: GoalState) -> dict:
        issues = [GoalIssue(**i) for i in state['validation_issues']]
        return {'context': gateway.goal_correction(state['allocations'], issues)}

    # one commit: the user only ever sees one current proposal, and a failed run leaves the last one standing
    def persist(state: GoalState) -> dict:
        with session_factory() as s:
            # assert_owned's UPDATE takes sqlite's write lock first, so the goals_changed check below can't be
            # overtaken by a decide_goal commit before this commits
            if claim is not None:
                claim.assert_owned(s)
            # the user accepted or changed a goal while this ran: the cap it planned within is stale, so write nothing
            if [c.id for c in committed_goals(s, state["user_id"])] != state["committed_ids"]:
                return {"status": "error", "reason": "goals_changed", "plans": []}
            _, expired = _supersede(s, state["user_id"], state["run_id"])
            start = date.fromisoformat(state["start"])
            rows = [Goal(user_id=state["user_id"], run_id=state["run_id"], kind=p.kind, priority=p.priority,
                         monthly_cents=p.monthly_cents, target_cents=p.target_cents, target_date=p.target_date,
                         start_date=start, strategy=p.strategy, rationale=p.rationale) for p in state["plans"]]
            s.add_all(rows)
            s.commit()
            goal_ids = [r.id for r in rows]
        # written already: a failure from here on mustn't report a run that proposed nothing while the proposal is live
        progress["goal_ids"] = goal_ids
        _delete_threads(checkpointer, expired)
        return {"status": "ok", "goal_ids": goal_ids}

    # a finished run with nothing to propose: the last proposal no longer fits. a failed run says nothing about that
    def withdraw(state: GoalState) -> dict:
        with session_factory() as s:
            if claim is not None:
                claim.assert_owned(s)
            withdrawn, expired = _supersede(s, state["user_id"], state["run_id"])
            s.commit()
        _delete_threads(checkpointer, expired)
        return {"withdrawn": withdrawn}

    # exit writes the final state to disk however the run ends, and deleting that checkpoint later only frees its rows.
    # the goal rows and the run summary hold what's needed: debt names and balances, the model's words and the prompt
    # stop here
    def forget_facts(state: GoalState) -> dict:
        return {"debts": [], "plans": [], "allocations": [], "proposal": [], "context": ""}

    # the goal rows are the source of truth, as for budget alerts: a decision commits before any resume, so one that
    # no resume reached still counts, and the [goal_id, decision] a resume carries only wakes this. no model from here
    def await_approval(state: GoalState) -> dict:
        while True:
            with session_factory() as s:
                rows = s.scalars(select(Goal).where(Goal.id.in_(state["goal_ids"]))).all()
                statuses = {g.id: g.status for g in rows}
            pending = sorted(i for i, status in statuses.items() if status == "proposed")
            if not pending:
                return {"decisions": statuses}
            interrupt({"waiting_for": pending})

    graph = StateGraph(GoalState)
    graph.add_node("load_facts", load_facts)
    graph.add_node("propose", propose)
    graph.add_node("validate_candidate", validate_candidate)
    graph.add_node("correction", correction)
    graph.add_node("persist", persist)
    graph.add_node("withdraw", withdraw)
    graph.add_edge(START, "load_facts")
    # with a checkpointer every ending goes through forget_facts. without one nothing reaches disk, and the cli prints
    # the plans from the result
    ending = END if checkpointer is None else "forget_facts"
    stopped = lambda state: ending if state.get("status") else None
    graph.add_conditional_edges("load_facts", lambda st: ending if st.get("status") == "error" else ("withdraw" if st.get("status") else "propose"))
    graph.add_conditional_edges("propose", lambda st: stopped(st) or "validate_candidate")
    graph.add_conditional_edges('validate_candidate', lambda st: stopped(st) or (
        'correction' if st['validation_issues'] else 'persist'))
    graph.add_edge('correction', 'propose')
    graph.add_edge("persist", ending)
    graph.add_edge("withdraw", ending)
    if checkpointer is not None:
        graph.add_node("forget_facts", forget_facts)
        graph.add_node("await_approval", await_approval)
        # only stored goals have anything to wait for. goals_changed wrote none, and a withdrawal isn't a proposal
        graph.add_conditional_edges("forget_facts", lambda st: (
            "await_approval" if st.get("status") == "ok" and st.get("goal_ids") else END))
        graph.add_edge("await_approval", END)
    return graph.compile(checkpointer=checkpointer)


def _summary(result: dict) -> dict:
    return {**{k: result.get(k) for k in ("surplus_source", "surplus_fallback_reason", "forecast_surplus_cents",
                                         "measured_surplus_cents", "forecast_as_of")},
            "outcome": result["status"], "reason": result.get("reason"),
            "surplus_cents": result.get("surplus_cents"), "cap_cents": result.get("cap_cents"),
            "card_minimums_cents": result.get("card_minimums_cents", 0),
            "gap_cents": result.get("gap_cents"), "goal_ids": result.get("goal_ids", []),
            "withdrawn": result.get("withdrawn", 0),
            "dropped": result.get("dropped", []), "attempts": result.get("attempts", 0),
            "validation_issues": result.get("validation_issues", []),
            "validation_history": result.get("validation_history", []),
            "decisions": {str(k): v for k, v in (result.get("decisions") or {}).items()}}


def _finish(session_factory, run_id: int, result: dict) -> None:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        row.status = "error" if result["status"] == "error" else "ok"
        row.finished_at = utcnow()
        row.summary_json = _summary(result)
        s.commit()


def _mark_paused(session_factory, run_id: int, result: dict) -> None:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        row.status = "awaiting_user"
        row.summary_json = _summary({**result, "status": "awaiting_user"})
        s.commit()


# the goal ids a paused run is waiting on; [] when it isn't paused or its checkpoint is gone. the run's row counts too:
# close expires a paused run and can't reach its checkpoint, so the checkpoint alone would still say it's waiting
def goal_waiting_for(session_factory, checkpointer: BaseCheckpointSaver, run_id: int) -> list[int]:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        if row is None or row.status != "awaiting_user":
            return []
    graph = build_goal_graph(session_factory, model=None, gateway=None, checkpointer=checkpointer)
    snapshot = graph.get_state(_thread(run_id))
    return sorted(i for task in snapshot.tasks for it in task.interrupts for i in it.value["waiting_for"])


# the caller holds the user's lock, and a pause doesn't: nothing is running while the run waits for the user
def run_goals_locked(session_factory, user_id: str, today: date, *, model: BaseChatModel, claim,
                     checkpointer: Optional[BaseCheckpointSaver] = None, audit_path=None,
                     on_start: Optional[Callable[[int], None]] = None) -> dict:
    with session_factory() as s:
        run = AgentRun(user_id=user_id, graph="goals", status="running")
        s.add(run)
        s.commit()
        run_id = run.id
    progress, waiting = {}, None
    # exit writes one checkpoint, when the graph stops; every step would put the debts on disk. an ending that doesn't
    # raise has been through forget_facts by then. one that raises hasn't, so its checkpoint holds the facts until the
    # finally below deletes it
    saving = {"config": _thread(run_id), "durability": "exit"} if checkpointer is not None else {}
    try:
        if checkpointer is not None:
            # sqlite reuses the id of a deleted run (seed --replace); a new run id never has history of its own
            checkpointer.delete_thread(_thread_id(run_id))
        if on_start is not None:
            on_start(run_id)        # the api answers with the id while the run goes on in the background
        with session_factory() as gs:
            gateway = PrivacyGateway(gs, user_id, audit_path=audit_path)    # one pseudonym per run
            graph = build_goal_graph(session_factory, model=model, gateway=gateway, progress=progress, claim=claim,
                                     checkpointer=checkpointer)
            result = graph.invoke({"user_id": user_id, "today": today.isoformat(), "run_id": run_id,
                                   'attempts': 0, 'validation_issues': [], 'validation_history': []}, **saving)
        waiting = _interrupted(result)
    except KeyboardInterrupt:
        _finish(session_factory, run_id, {**progress, "status": "error", "reason": "Interrupted"})
        raise
    except Exception as exc:
        # an unreachable model, say: the run says so and the last proposal stands
        logger.warning("goal run failed: %s", type(exc).__name__)
        result = {**progress, "status": "error", "reason": type(exc).__name__, "plans": []}
    finally:
        # only a paused run needs its checkpoint
        if waiting is None:
            _delete_threads(checkpointer, [run_id])
    result = {"goal_ids": [], "dropped": [], **result, "run_id": run_id}
    if waiting is None:
        _finish(session_factory, run_id, result)
        return {**result, "waiting_for": []}
    _mark_paused(session_factory, run_id, result)
    # a decision made after the run read its goals but before this mark found no awaiting run to resume
    with session_factory() as s:
        decided = s.execute(select(Goal.id, Goal.status).where(Goal.id.in_(waiting), Goal.status != "proposed")
                            .order_by(Goal.id)).first()
    if decided is not None:
        try:
            return {**result, **resume_goal_run(session_factory, run_id, decided.id, decided.status,
                                                checkpointer=checkpointer)}
        except Exception:
            logger.warning("goal run %s: resume of an early decision failed", run_id, exc_info=True)
    return {**result, "status": "awaiting_user", "waiting_for": waiting}


# one goal run at a time for a user, and not during a budget run: they read the same money. a busy user writes nothing.
# A renewable database claim coordinates API and CLI processes; goal writes verify its owner token.
def run_goals(session_factory, user_id: str, today: date, *, model: Optional[BaseChatModel] = None,
              audit_path=None, checkpointer: Optional[BaseCheckpointSaver] = None) -> dict:
    lock = user_lock(user_id)
    if not lock.acquire(blocking=False, session_factory=session_factory):
        return {"status": "busy", "goal_ids": [], "dropped": []}
    try:
        if running_elsewhere(session_factory, user_id):
            return {"status": "busy", "goal_ids": [], "dropped": []}
        return run_goals_locked(session_factory, user_id, today, model=model or get_chat_model(), claim=lock,
                                checkpointer=checkpointer, audit_path=audit_path)
    finally:
        lock.release()


# continue a paused run after one decision. it pauses again while another goal is still proposed. no model: nothing
# after the pause calls one, so a broken model setting can't block a decision
def resume_goal_run(session_factory, run_id: int, goal_id: int, decision: str, *,
                    checkpointer: BaseCheckpointSaver) -> dict:
    with _resume_lock(run_id):
        # another resume may have finished the run (or taken this decision) while this one waited for the lock
        waiting = goal_waiting_for(session_factory, checkpointer, run_id)
        if goal_id not in waiting:
            with session_factory() as s:
                status = s.get(AgentRun, run_id).status
            return {"run_id": run_id, "status": status, "waiting_for": waiting}
        graph = build_goal_graph(session_factory, model=None, gateway=None, checkpointer=checkpointer)
        try:
            result = graph.invoke(Command(resume=[goal_id, decision]), _thread(run_id), durability="exit")
        except Exception as exc:
            # still paused means nothing ran: the run keeps waiting and can be resumed again. only a run that got past
            # the pause and then failed is an error
            if not goal_waiting_for(session_factory, checkpointer, run_id):
                _finish(session_factory, run_id, {"status": "error", "reason": type(exc).__name__})
            raise
        waiting = _interrupted(result)
        if waiting is not None:
            return {**result, "status": "awaiting_user", "waiting_for": waiting}
        _finish(session_factory, run_id, result)
        _delete_threads(checkpointer, [run_id])
        return {**result, "waiting_for": []}


# a paused run whose checkpoint is gone can't resume, so its rows finish it, once none is still proposed
def finish_paused_run(session_factory, run_id: int) -> None:
    with session_factory() as s:
        row = s.get(AgentRun, run_id)
        if row is None or row.status != "awaiting_user":
            return
        summary = row.summary_json or {}
        goals = s.scalars(select(Goal).where(Goal.id.in_(summary.get("goal_ids", [])))).all()
        if any(g.status == "proposed" for g in goals):
            return
        row.status, row.finished_at = "ok", utcnow()
        row.summary_json = {**summary, "outcome": "ok", "decisions": {str(g.id): g.status for g in goals}}
        s.commit()


def _dollars(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _print_report(state: dict) -> None:
    status = state["status"]
    if status == "busy":
        print("A budget or goal run is already in progress for this user.")
        return
    if status == "error":
        print(f"run failed: {state.get('reason')}")
        print(f"{state.get('attempts', 0)} model calls; No replacement was generated; "
              "any earlier proposal remains unchanged.")
        for issue in state.get('validation_issues', []):
            label = f"goal {issue['index']} ({issue['kind']}): " if issue.get('index') else ''
            print(f"   {label}{issue['message']}")
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
    if status == 'no_affordable_goals':
        print('No goals: the cap is below the $1.00 minimum monthly amount; the model was not called.')
        if withdrawn:
            print(withdrawn)
        return
    if status == "nothing_offered":
        print("No goals: the preferences or goals you've accepted cover every kind that's available.")
        if withdrawn:
            print(withdrawn)
        return
    print(f"Validated in {state.get('attempts', 0)} model calls.")
    for p in state["plans"]:
        when = p.target_date.strftime("%Y-%m")
        strategy = f" ({p.strategy})" if p.strategy else ""
        print(f"{p.priority}. {p.kind}{strategy}: {_dollars(p.monthly_cents)}/mo, target "
              f"{_dollars(p.target_cents)}, {when}")
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
