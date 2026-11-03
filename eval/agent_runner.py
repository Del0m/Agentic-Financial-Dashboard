import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app import config
from app.agents.goal_graph import run_goals
from app.budget_service.goal_prefs import set_goal_prefs
from app.budget_service.simulate import advance_days
from app.db.models import AgentRun, Alert
from eval.budget_users import profile_by_id, seed_profile
from eval.fixtures import BudgetScenario, GoalScenario, temp_db

# budget and goal scenarios, through the real daily run and goal run (spec: tpc-26). like runner.run_once these never
# raise: a failed run is a result to score, not a reason to stop the eval


@dataclass
class BudgetRecord:
    scenario_id: str
    run: int
    flags: dict[str, str] = field(default_factory=dict)     # category -> the first day it was flagged
    alerts: list[dict] = field(default_factory=list)        # in the order written, each with the day of its run
    llm_failures: int = 0
    llm_calls: int = 0
    llm_retries: int = 0
    latency_s: float = 0.0
    error: Optional[str] = None
    kind: str = "budget"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoalRecord:
    scenario_id: str
    run: int
    status: Optional[str] = None
    reason: Optional[str] = None
    cap_cents: Optional[int] = None
    surplus_cents: Optional[int] = None
    offered: list[str] = field(default_factory=list)
    attempts: int = 0
    plans: list[dict] = field(default_factory=list)         # in priority order
    latency_s: float = 0.0
    error: Optional[str] = None
    kind: str = "goal"

    def to_dict(self) -> dict:
        return asdict(self)


def _alert(row: Alert, day: str) -> dict:
    return {"day": day, "kind": row.kind, "category": row.category, "cause": row.cause, "action": row.action,
            "severity": row.severity, "message": row.message, "signal_days": row.signal_days}


# advance_days takes no audit path and a daily run builds its own gateway, so the setting itself points at the run's
# folder for as long as the run lasts. prompts from an eval must never reach the app's real audit log
def run_budget_once(scenario: BudgetScenario, *, model, audit_path: Path, run: int = 1) -> BudgetRecord:
    rec = BudgetRecord(scenario_id=scenario.id, run=run)
    start, previous = None, config.PROMPT_AUDIT_PATH
    config.PROMPT_AUDIT_PATH = audit_path
    try:
        with temp_db() as Session:
            with Session() as s:
                user_id = seed_profile(s, profile_by_id(scenario.profile), until=scenario.start)
            seen = set()

            def on_day(day: dict) -> None:
                for category in day["flags"]:
                    rec.flags.setdefault(category, day["date"])
                with Session() as s:
                    for row in s.scalars(select(Alert).where(Alert.user_id == user_id).order_by(Alert.id)):
                        if row.id not in seen:
                            seen.add(row.id)
                            rec.alerts.append(_alert(row, day["date"]))

            start = time.perf_counter()
            advance_days(Session, user_id, (scenario.end - scenario.start).days, model=model, checkpointer=None,
                         on_day=on_day)
            rec.latency_s = time.perf_counter() - start
            with Session() as s:
                summaries = s.scalars(select(AgentRun.summary_json).where(AgentRun.user_id == user_id,
                                                                          AgentRun.graph == "budget"))
                summaries = [summary or {} for summary in summaries]
                rec.llm_failures = sum(summary.get("llm_failures", 0) for summary in summaries)
                rec.llm_calls = sum(summary.get("llm_calls", 0) for summary in summaries)
                rec.llm_retries = sum(summary.get("llm_retries", 0) for summary in summaries)
    except Exception as exc:
        rec.error = f"{type(exc).__name__}: {exc}"
        rec.latency_s = time.perf_counter() - start if start is not None else 0.0
    finally:
        config.PROMPT_AUDIT_PATH = previous
    return rec


def run_goal_once(scenario: GoalScenario, *, model, audit_path: Path, run: int = 1) -> GoalRecord:
    rec = GoalRecord(scenario_id=scenario.id, run=run)
    start = None
    try:
        with temp_db() as Session:
            with Session() as s:
                user_id = seed_profile(s, profile_by_id(scenario.profile))
                if scenario.prefs is not None:
                    set_goal_prefs(s, user_id, scenario.prefs)
                    s.commit()
            start = time.perf_counter()
            state = run_goals(Session, user_id, scenario.today, model=model, audit_path=audit_path)
            rec.latency_s = time.perf_counter() - start
        rec.status, rec.reason = state["status"], state.get("reason")
        rec.cap_cents, rec.surplus_cents = state.get("cap_cents"), state.get("surplus_cents")
        rec.offered, rec.attempts = list(state.get("offered", [])), state.get("attempts", 0)
        rec.plans = [{"kind": p.kind, "priority": p.priority, "monthly_cents": p.monthly_cents, "strategy": p.strategy,
                      "target_cents": p.target_cents, "target_date": p.target_date.isoformat() if p.target_date else None,
                      "rationale": p.rationale} for p in state.get("plans", [])]
    except Exception as exc:
        rec.error = f"{type(exc).__name__}: {exc}"
        rec.latency_s = time.perf_counter() - start if start is not None else 0.0
    return rec
