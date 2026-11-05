from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.budget_engine.close import close_period, period_actuals
from app.budget_engine.fit import HISTORY_MONTHS, fit_budget
from app.budget_engine.goals import budgeted_in, finished_before
from app.budget_engine.groups import default_group, default_name
from app.budget_engine.pace import BudgetLine as EngineLine
from app.budget_engine.recurring import monthly_cents
from app.budget_engine.signals import cancel_limit
from app.budget_engine.styles import apply_style, fact_message
from app.budget_engine.txns import (EARLY_DAYS, FREE, GOAL_CATEGORIES, GOAL_DEBT_PAYOFF, GOAL_EMERGENCY_FUND,
                                    GOAL_SAVINGS, LOAN_PAYMENTS, SAVINGS, add_months, category_totals, month_end,
                                    net_of_goal, savings)
from app.budget_service.goal_lines import committed_goals, expire_goal_proposals, stale_proposals
from app.budget_service.initialize import history_start, load_txns
from app.budget_service.settings import user_style
from app.db.models import AdviceLog, AgentRun, Alert, BudgetLine, BudgetPeriod, Goal, RecurringStream, utcnow
from app.kg import project

# period close (tpc-22, architecture 6.5): the db side of the close graph. gather reads the closed month,
# adapt is pure (close_period for each line, then the user's style), and persist writes the whole month in
# one commit: the old period closed, undecided proposals expired, finished goals completed, the next period and its
# lines, any alerts, and the run row. the (user_id, start_date) constraint means a rerun or a race can never open a
# month twice

SUMMARY_LINE_KEYS = ("category", "name", "group", "old_cents", "actual_cents", "new_cents", "rule")


# the oldest open period that ended before today; a month with no budget, or one already closed, has none
def due_period(session: Session, user_id: str, today: date) -> Optional[BudgetPeriod]:
    return session.scalars(select(BudgetPeriod).where(
        BudgetPeriod.user_id == user_id, BudgetPeriod.status == "open", BudgetPeriod.end_date < today)
        .order_by(BudgetPeriod.start_date).limit(1)).first()


# adjust_next_budget proposals still waiting when their month closes count as dismissed (architecture 6.6)
def pending_proposals(session: Session, period_id: int) -> list[int]:
    return list(session.scalars(select(Alert.id).where(
        Alert.period_id == period_id, Alert.action == "adjust_next_budget", Alert.status == "pending_user")
        .order_by(Alert.id)))


# marks the alerts dismissed, their advice ignored, and the budget runs paused on them expired; the caller commits.
# the status is checked again here, in the close's own transaction: a user who answered while close was running
# keeps their answer. returns the ids actually expired
def expire_proposals(session: Session, alert_ids: list[int]) -> list[int]:
    if not alert_ids:
        return []
    alerts = session.scalars(select(Alert).where(Alert.id.in_(alert_ids), Alert.status == "pending_user")).all()
    for alert in alerts:
        alert.status = "dismissed"
    alert_ids = sorted(a.id for a in alerts)
    if not alert_ids:
        return []
    advice = session.scalars(select(AdviceLog).where(AdviceLog.alert_id.in_(alert_ids))).all()
    for row in advice:
        row.outcome = "ignored"
    runs = session.scalars(select(AgentRun).where(
        AgentRun.id.in_({row.run_id for row in advice if row.run_id is not None}),
        AgentRun.status == "awaiting_user")).all()
    for run in runs:
        waited = sorted(row.alert_id for row in advice if row.run_id == run.id)
        run.status, run.finished_at = "expired", utcnow()
        run.summary_json = {**(run.summary_json or {}), "expired_alert_ids": waited}
    return alert_ids


# next month's goal lines and the goals that ended before it, from the rows as this session reads them
def _goal_commitments(session: Session, period: BudgetPeriod, new_start: date) -> tuple[list[dict], list[int]]:
    names = {ln.category: ln.name for ln in period.lines}
    goals = []
    committed = committed_goals(session, period.user_id)
    for goal in committed:
        if budgeted_in(goal, new_start):
            category = GOAL_CATEGORIES[goal.kind]
            goals.append({"category": category, "name": names.get(category, default_name(category)),
                          "monthly_cents": goal.monthly_cents, "goal_id": goal.id})
    return goals, [g.id for g in committed if finished_before(g, new_start)]


def gather_month(session: Session, period_id: int) -> dict:
    period = session.get(BudgetPeriod, period_id)
    start, end = period.start_date, period.end_date
    # from a few days early: this month's fixed bill paid at the end of the last one counts for this month
    txns = load_txns(session, period.user_id, start - timedelta(days=EARLY_DAYS), end)
    spent = period_actuals([EngineLine(ln.category, ln.kind, ln.limit_cents, ln.group) for ln in period.lines],
                           txns, start)
    saved = sum(category_totals(savings(txns), start, end).values())
    # a goal's payment shows up as loan spending or saving. the closed month's goal lines say how much is the goal's,
    # so the fixed lines don't adopt it (the goal line already budgets it) and it doesn't land on free (spec, double
    # counting). these two are net from here on
    limits = {ln.category: ln.limit_cents for ln in period.lines}
    spent[LOAN_PAYMENTS] = net_of_goal(spent.get(LOAN_PAYMENTS, 0), limits.get(LOAN_PAYMENTS, 0),
                                       limits.get(GOAL_DEBT_PAYOFF, 0))
    saved = net_of_goal(saved, limits.get(SAVINGS, 0),
                        limits.get(GOAL_EMERGENCY_FUND, 0) + limits.get(GOAL_SAVINGS, 0))
    lined = {ln.category for ln in period.lines}
    unlined = {c: amount for c, amount in spent.items() if c not in lined and amount > 0}
    # what the free line covered, whether or not the period had one (a budget from before tpc-22 didn't)
    free_actual = spent.get(FREE, 0) + sum(amount for c, amount in unlined.items() if c != FREE)

    def actual(ln: BudgetLine) -> int:
        if ln.group == "free":
            return free_actual
        if ln.group == "saving":
            return saved
        return spent.get(ln.category, 0)

    accepted = {}
    for alert in session.scalars(select(Alert).where(
            Alert.period_id == period_id, Alert.action == "adjust_next_budget", Alert.status == "accepted",
            Alert.proposed_limit_cents.is_not(None)).order_by(Alert.id)):
        accepted[alert.category] = alert.proposed_limit_cents       # the latest answer wins

    # pending cancellations survive close. consume each accepted stream once, including an answer to an older month
    consumed = {outcome["stream_id"] for summary in session.scalars(select(AgentRun.summary_json).where(
        AgentRun.user_id == period.user_id, AgentRun.graph == "close", AgentRun.status == "ok"))
        for outcome in (summary or {}).get("cancel_outcomes", [])}
    cancel_outcomes = []
    for alert, stream in session.execute(select(Alert, RecurringStream).join(
            RecurringStream, Alert.stream_id == RecurringStream.id).where(
            Alert.user_id == period.user_id, RecurringStream.user_id == period.user_id,
            Alert.action == "suggest_cancel", Alert.status == "accepted").order_by(Alert.id)):
        if stream.id in consumed:
            continue
        consumed.add(stream.id)
        category = stream.category
        old = accepted.get(category, limits.get(category))
        new = None if old is None else cancel_limit(old, monthly_cents(stream.typical_cents, stream.frequency))
        outcome = "no_line" if old is None else "nonpositive" if new is None else "applied"
        if new is not None:
            accepted[category] = new
        cancel_outcomes.append({"alert_id": alert.id, "stream_id": stream.id, "category": category,
                                "old_cents": old, "new_cents": new, "outcome": outcome})

    new_start = end + timedelta(days=1)
    # a month further back than it fits, so the window's edge doesn't look like the start of the history
    history = load_txns(session, period.user_id, add_months(new_start, -HISTORY_MONTHS - 1), end)
    fit = fit_budget(history, new_start, history_start=history_start(session, period.user_id, end))
    style, targets = user_style(session, period.user_id)
    goals, finished_goal_ids = _goal_commitments(session, period, new_start)
    return {
        "month": start, "new_start": new_start, "user_id": period.user_id,
        "lines": [{"category": ln.category, "name": ln.name, "group": ln.group, "kind": ln.kind,
                   "limit_cents": ln.limit_cents, "actual_cents": actual(ln)} for ln in period.lines],
        "saved_cents": saved, "free_actual_cents": free_actual, "unbudgeted": sorted(c for c in unlined if c != FREE),
        "accepted": accepted, "goals": goals, "cancel_outcomes": cancel_outcomes,
        "finished_goal_ids": finished_goal_ids,
        "expired_goal_ids": stale_proposals(session, period.user_id, new_start),
        "income_cents": fit.income_cents, "fallback_total_cents": fit.spending_total_cents,
        "style": style, "targets": targets,
    }


def goal_ended_message(name: str, cents: int) -> str:
    return (f"Your {name} goal reached its planned end date, so its ${cents / 100:,.2f} budget line has ended. "
            f"Check that it's done, and generate goals again if there's more to do.")


def _saving_line(actual_cents: int, new_cents: int, rule: str) -> dict:
    return {"category": SAVINGS, "name": default_name(SAVINGS), "group": "saving", "kind": "fixed", "limit_cents": 0,
            "actual_cents": actual_cents, "old_cents": 0, "new_cents": new_cents, "rule": rule}


# pure: next month's lines from the closed one. free is never adjusted here; the style sets it. a goal's line is
# never adjusted either: it's the amount the user accepted, for as long as the goal runs
def adapt_month(gathered: dict) -> dict:
    lines = [ln for ln in gathered["lines"] if ln["group"] not in ("free", "goal")]
    actuals = {ln["category"]: ln["actual_cents"] for ln in lines}
    adjustments = close_period([EngineLine(ln["category"], ln["kind"], ln["limit_cents"], ln["group"])
                                for ln in lines], actuals, gathered["accepted"])
    new = [{**ln, "old_cents": adj.old_cents, "new_cents": adj.new_cents, "rule": adj.source}
           for ln, adj in zip(lines, adjustments)]
    # a budget from before tpc-22 gains a saving line once the user is seen saving
    if gathered["saved_cents"] and all(ln["category"] != SAVINGS for ln in new):
        new.append(_saving_line(gathered["saved_cents"], gathered["saved_cents"], "fixed"))
    old_limits = {ln["category"]: ln["limit_cents"] for ln in gathered["lines"]}
    for goal in gathered["goals"]:
        old_cents = old_limits.get(goal["category"], 0)
        new.append({"category": goal["category"], "name": goal["name"], "group": "goal", "kind": "fixed",
                    "limit_cents": old_cents, "actual_cents": 0, "old_cents": old_cents,
                    "new_cents": goal["monthly_cents"], "rule": "goal"})

    result = apply_style(gathered["style"], [EngineLine(ln["category"], ln["kind"], ln["new_cents"], ln["group"])
                                             for ln in new], gathered["income_cents"],
                         gathered["fallback_total_cents"], gathered["targets"])
    split = gathered["style"] == "50_30_20" and result.fallback is None
    if SAVINGS in result.limits and all(ln["category"] != SAVINGS for ln in new):
        new.append(_saving_line(0, 0, "target"))          # 50/30/20 sets one the history didn't show
    for ln in new:
        ln["new_cents"] = result.limits[ln["category"]]
        if split and ln["category"] == SAVINGS:
            ln["rule"] = "target"

    old_free = next((ln for ln in gathered["lines"] if ln["group"] == "free"), None)
    old_cents = old_free["limit_cents"] if old_free else 0
    new.append({"category": FREE, "name": old_free["name"] if old_free else default_name(FREE), "group": "free",
                "kind": "flexible", "limit_cents": old_cents, "actual_cents": gathered["free_actual_cents"],
                "old_cents": old_cents, "new_cents": result.limits[FREE], "rule": "remainder"})
    return {"lines": new, "total_cents": result.total_cents, "free_cents": result.limits[FREE],
            "facts": list(result.facts), "style_fallback": result.fallback}


# one commit for the whole month; returns the run's summary and its id. None when another close got there first (the old period is no longer open, or
# the next one already exists): nothing is written
def persist_month(session_factory, period_id: int, gathered: dict, adapted: dict,
                  expired: list[int]) -> Optional[dict]:
    with session_factory() as s:
        old = s.get(BudgetPeriod, period_id)
        if old.status != "open":
            return None
        # written first: this takes the write lock, so no goal decision can land between the check below and the commit
        old.status = "closed"
        s.flush()
        expired = expire_proposals(s, expired)
        expired_goals = expire_goal_proposals(s, gathered["expired_goal_ids"])
        new_start = gathered["new_start"]
        # a goal decided while close was running changes next month's lines, so they're worked out again from the rows
        goals, finished_goal_ids = _goal_commitments(s, old, new_start)
        if goals != gathered["goals"]:
            adapted = adapt_month({**gathered, "goals": goals})
        # a goal the user stopped while close was running stays stopped, and gets no alert
        finished = s.execute(update(Goal).where(Goal.id.in_(finished_goal_ids), Goal.status == "accepted")
                             .values(status="completed").returning(Goal.id, Goal.kind, Goal.monthly_cents)).all()
        period = BudgetPeriod(user_id=old.user_id, start_date=new_start, end_date=month_end(new_start),
                              status="open", fit_json=None, income_cents=gathered["income_cents"],
                              total_cents=adapted["total_cents"])
        period.lines = [BudgetLine(category=ln["category"], name=ln["name"], group=ln["group"], kind=ln["kind"],
                                   limit_cents=ln["new_cents"],
                                   source="user" if ln["rule"] in ("accepted", "goal") else "adapted",
                                   fit_json={"old_cents": ln["old_cents"], "actual_cents": ln["actual_cents"],
                                             "rule": ln["rule"]})
                        for ln in adapted["lines"]]
        s.add(period)
        try:
            s.flush()
        except IntegrityError:
            s.rollback()
            return None
        messages = [(f.kind, fact_message(f)) for f in adapted["facts"]]
        # nothing tracks contributions, so a line ending says only that the plan did, not that the debt is paid
        names = {ln.category: ln.name for ln in old.lines}
        for _, kind, cents in sorted(finished):
            category = GOAL_CATEGORIES[kind]
            messages.append(("goal_ended", goal_ended_message(names.get(category, default_name(category)), cents)))
        alerts = [Alert(user_id=old.user_id, period_id=period.id, kind=kind, category=None, severity="medium",
                        cause=None, action="alert", message=message, status="pending_user")
                  for kind, message in messages]
        s.add_all(alerts)
        s.flush()
        summary = {
            "month": gathered["month"].isoformat(), "new_period_id": period.id,
            "lines": [{k: ln[k] for k in SUMMARY_LINE_KEYS} for ln in adapted["lines"]],
            "expired_alert_ids": expired, "expired_goal_ids": expired_goals,
            "finished_goal_ids": sorted(g.id for g in finished), "income_cents": gathered["income_cents"],
            "total_cents": adapted["total_cents"], "free_cents": adapted["free_cents"], "style": gathered["style"],
            "alert_ids": [a.id for a in alerts], "unbudgeted": gathered["unbudgeted"],
            "cancel_outcomes": gathered.get("cancel_outcomes", []),
        }
        if adapted["style_fallback"]:
            summary["style_fallback"] = adapted["style_fallback"]
        run = AgentRun(user_id=old.user_id, graph="close", status="ok", finished_at=utcnow(), summary_json=summary)
        s.add(run)
        # the closed month's final actuals and the new period's limits (tpc-36)
        def project_close() -> None:
            project.project_month(s, old.user_id, gathered["month"])
            project.project_period(s, old.user_id, period)
            project.project_advice(s, old.user_id)
        project.safely(s, "close", project_close)
        s.commit()
        return {**summary, "run_id": run.id}


def record_error(session_factory, user_id: str, month: Optional[date], error: str) -> None:
    with session_factory() as s:
        s.add(AgentRun(user_id=user_id, graph="close", status="error", finished_at=utcnow(),
                       summary_json={"month": month.isoformat() if month else None, "error": error}))
        s.commit()


# closes every month that ended before today, oldest first; one summary per month closed. the caller holds the
# user's lock (budget_service/daily.py)
def close_due(session_factory, user_id: str, today: date) -> list[dict]:
    from app.agents.close_graph import build_close_graph      # the graph calls back into this module
    state = build_close_graph(session_factory).invoke({"user_id": user_id, "today": today.isoformat(), "closed": []},
                                                      {"recursion_limit": 500})
    return state["closed"]
