from datetime import date
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.budget_service import close as close_service

# the period-close agent (tpc-22, architecture 4.3): load_period -> expire_proposals -> gather -> adapt ->
# persist, then back to load_period while another month is due, so a missed month-end or a gap of several months
# closes one month at a time, each from its own actuals. no llm: every number is code's, and nothing pauses.
# a failing step rolls the month back and records an error run; the next daily run tries again


class CloseState(TypedDict, total=False):
    user_id: str
    today: str                      # iso
    period_id: Optional[int]
    month: Optional[date]
    expired: list[int]
    gathered: dict
    adapted: dict
    status: str                     # due | done | closed | error
    error: str
    closed: list[dict]              # one summary per month closed, in order


def build_close_graph(session_factory):
    def guarded(fn):
        def node(state: CloseState) -> dict:
            try:
                return fn(state)
            except Exception as exc:
                return {"status": "error", "error": type(exc).__name__}
        return node

    def load_period(state: CloseState) -> dict:
        with session_factory() as s:
            period = close_service.due_period(s, state["user_id"], date.fromisoformat(state["today"]))
            if period is None:
                return {"status": "done", "period_id": None}
            return {"status": "due", "period_id": period.id, "month": period.start_date}

    def expire_proposals(state: CloseState) -> dict:
        with session_factory() as s:
            return {"expired": close_service.pending_proposals(s, state["period_id"])}

    def gather(state: CloseState) -> dict:
        with session_factory() as s:
            return {"gathered": close_service.gather_month(s, state["period_id"])}

    def adapt(state: CloseState) -> dict:
        return {"adapted": close_service.adapt_month(state["gathered"])}

    # the expiry from expire_proposals is written here, in the same commit as the new month
    def persist(state: CloseState) -> dict:
        summary = close_service.persist_month(session_factory, state["period_id"], state["gathered"],
                                              state["adapted"], state["expired"])
        if summary is None:
            return {"status": "done"}          # another close got there first
        return {"status": "closed", "closed": [*state["closed"], summary]}

    def record_error(state: CloseState) -> dict:
        close_service.record_error(session_factory, state["user_id"], state.get("month"), state["error"])
        return {}

    def next_step(target: str):
        def route(state: CloseState) -> str:
            return "record_error" if state.get("status") == "error" else target
        return route

    def after_load(state: CloseState) -> str:
        if state.get("status") == "error":
            return "record_error"
        return "expire_proposals" if state["status"] == "due" else END

    def after_persist(state: CloseState) -> str:
        if state.get("status") == "error":
            return "record_error"
        return "load_period" if state["status"] == "closed" else END

    graph = StateGraph(CloseState)
    for name, fn in (("load_period", load_period), ("expire_proposals", expire_proposals), ("gather", gather),
                     ("adapt", adapt), ("persist", persist)):
        graph.add_node(name, guarded(fn))
    graph.add_node("record_error", record_error)
    graph.add_edge(START, "load_period")
    graph.add_conditional_edges("load_period", after_load, ["expire_proposals", "record_error", END])
    graph.add_conditional_edges("expire_proposals", next_step("gather"), ["gather", "record_error"])
    graph.add_conditional_edges("gather", next_step("adapt"), ["adapt", "record_error"])
    graph.add_conditional_edges("adapt", next_step("persist"), ["persist", "record_error"])
    graph.add_conditional_edges("persist", after_persist, ["load_period", "record_error", END])
    graph.add_edge("record_error", END)
    return graph.compile()
