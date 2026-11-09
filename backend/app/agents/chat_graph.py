import time
from datetime import date, timedelta
from typing import Annotated, Callable, Optional
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from .. import config
from ..budget_engine.txns import add_months, month_start
from .llm import _run_tool, get_chat_model

# the m2 chat agent (architecture section 4): retrieve, then a hand-built react loop, agent <-> tools.
# hand-built rather than create_react_agent so tool errors go through _run_tool, which only
# forwards text meant for the model (the prebuilt ToolNode sends raw exception text back).
# the app retrieves guidance and lists the sources itself: in live runs neither llama3.1:8b nor
# qwen2.5:14b called search_kb next to get_debts, and both invented citations (spec amendment a1)

SYSTEM_PROMPT = (
    "You are a personal finance assistant for one user. For anything about the user's own accounts, "
    "debts or spending, call get_accounts, get_debts or get_spending and use only the numbers they return; "
    "never guess the user's numbers. A question about \"my\" debts or cards needs the lookup even when the "
    "guidance below covers the topic. For which debt to pay first, call get_debts, name the debt with the highest "
    "APR and give its APR and balance; if there are no debts, say the user has no debts. For recurring charges, subscriptions or how much do I pay monthly, "
    "call get_recurring. For cash before payday, call get_cash_forecast and repeat its summary, with "
    "every figure and any bill in it. For how long until debt-free, call plan_debt_payoff and repeat "
    "its summary, with every figure in it. For general rules and strategies, use the guidance below and refer to "
    "it by its title; never write URLs or name other websites, because the app lists the sources for you. "
    "If the guidance doesn't cover the question, call search_kb. When a figure applies to a specific year, "
    'say which year. Keep answers short and concrete. End with one line: "This is general information, '
    'not financial advice."'
)
GUIDANCE_SENTENCE = ("For general rules and strategies, use the guidance below and refer to it by its title; never "
                     "write URLs or name other websites, because the app lists the sources for you. ")
SEARCH_SENTENCE = "If the guidance doesn't cover the question, call search_kb. "
LOOKUP_GUIDANCE_CLAUSE = " even when the guidance below covers the topic"
# which chat retrieval modes use the vector knowledge base (tpc-27)
VECTOR_MODES = ("vector", "hybrid", "hybrid-shuffled")
# the graph modes need graph_retrieve (tpc-39); until then the eval refuses them instead of measuring nothing
GRAPH_MODES = ("graph", "hybrid", "hybrid-shuffled")
GRAPH_RETRIEVAL_BUILT = True
GRAPH_SENTENCE = ("For a past date or a change over time, answer from the facts below, which hold for the date they give, "
                  "and look them up only if none are given. If they give no figure for the year asked, say you "
                  "don't have it, even if the guidance gives one for another year. ")
DISCLAIMER = "This is general information, not financial advice."
DISCLAIMER_MARK = "not financial advice"
# a model that keeps calling tools gets one last call without them, so the user still gets an answer
MAX_TOOL_ROUNDS = 6


# the model must not be told about guidance or a tool the condition took away, or it could still reach for them.
# so these conditions change the prompt too, and the paper says so
def system_prompt() -> str:
    if config.KG_CHAT == "none":
        return SYSTEM_PROMPT.replace(GUIDANCE_SENTENCE, "").replace(SEARCH_SENTENCE, "").replace(LOOKUP_GUIDANCE_CLAUSE, "")
    if config.KG_CHAT == "graph":
        return SYSTEM_PROMPT.replace(GUIDANCE_SENTENCE, GRAPH_SENTENCE).replace(SEARCH_SENTENCE, "").replace(LOOKUP_GUIDANCE_CLAUSE, "")
    if config.KG_CHAT in GRAPH_MODES:
        return SYSTEM_PROMPT.replace(SEARCH_SENTENCE, SEARCH_SENTENCE + GRAPH_SENTENCE)
    return SYSTEM_PROMPT


def _merge(old: Optional[dict], new: Optional[dict]) -> dict:
    return {**(old or {}), **(new or {})}


class ChatState(MessagesState):
    pseudonym: str      # the privacy gateway's pseudonym for this thread, reused every turn
    graph_facts: Optional[dict]     # what the graph says about the current question: as_of and fact lines
    graph_by_turn: Annotated[dict[str, bool], _merge]   # question message id -> were graph facts used, for history
    tool_rounds: int    # tool rounds in the current turn; retrieve resets it to 0 per question
    sources: list[dict]  # guidance retrieved for the current question
    sources_by_turn: Annotated[dict[str, list[dict]], _merge]  # question message id -> its sources, for history

# what history shows under an answer; the passage text is only needed for the current question
CITATION_KEYS = ("title", "section", "source_url")
INTERRUPTED = "error: the previous turn ended before this tool returned"


# without it a model guesses the year from its training data, so "last month" searches the wrong dates.
# the month boundaries are spelled out because a small model reads "last month" as the last 30 days
def _date_line(today: Optional[Callable[[], date]]) -> str:
    if today is None:
        return ""
    day = today()
    this_month = month_start(day)
    last_month = add_months(this_month, -1)
    return (f"\n\nToday's date is {day.isoformat()}. This month means {this_month.isoformat()} to {day.isoformat()}. "
            f"Last month means {last_month.isoformat()} to {(this_month - timedelta(days=1)).isoformat()}. "
            f"The last 30 days means {(day - timedelta(days=30)).isoformat()} to {day.isoformat()}.")


def _guidance(sources: list[dict]) -> str:
    if not sources:
        return ""
    return "\n\nGuidance retrieved for this question:\n" + "\n".join(
        f"- {s['title']} / {s['section']}: {s['text']}" for s in sources)


# a turn cut off mid-tool (closed tab, ollama timeout) leaves tool calls with no results in the checkpoint.
# the model is sent a stand-in result for each, so every later turn's history stays well formed
def _graph_section(found: Optional[dict]) -> str:
    if not found or not found.get("facts"):
        return ""
    return f"\n\nFacts (valid as of {found['as_of']}):\n" + "\n".join(f"- {line}" for line in found["facts"])


def _answered(messages: list) -> list:
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    out = []
    for m in messages:
        out.append(m)
        out += [ToolMessage(INTERRUPTED, tool_call_id=tc["id"], name=tc["name"])
                for tc in getattr(m, "tool_calls", None) or [] if tc["id"] not in answered]
    return out


def build_chat_graph(tools: list[BaseTool], *, retrieve: Optional[Callable[[str], list[dict]]] = None,
                     model: Optional[BaseChatModel] = None, checkpointer: Optional[BaseCheckpointSaver] = None,
                     today: Optional[Callable[[], date]] = None, graph_retrieve: Optional[Callable[[str], dict]] = None):
    model = model or get_chat_model()
    by_name = {t.name: t for t in tools}

    def retrieve_guidance(state: ChatState) -> dict:
        question = next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
        found = retrieve(question.content) if retrieve and config.KG_CHAT in VECTOR_MODES else []
        # the round count is per question: resetting it here means no caller can forget to
        return {"sources": found, "tool_rounds": 0,
                "sources_by_turn": {question.id: [{k: s[k] for k in CITATION_KEYS} for s in found]}}

    # app-side like retrieve, because local models skip retrieval tools (decision log, 2026-10-26)
    def graph_node(state: ChatState) -> dict:
        question = next(m for m in reversed(state["messages"]) if isinstance(m, HumanMessage))
        found = graph_retrieve(question.content) if graph_retrieve and config.KG_CHAT in GRAPH_MODES else None
        return {"graph_facts": found, "graph_by_turn": {question.id: bool(found and found.get("facts"))}}

    # the system prompt, date and guidance are sent every call but never stored, so none go stale in old threads
    def agent(state: ChatState) -> dict:
        # offered get_history with the answer already in the facts, the local model wrote an empty answer or one in
        # another language. with it left out, it answered from the facts. so it's only there when they found nothing
        have_facts = bool((state.get("graph_facts") or {}).get("facts"))
        offered = [t for t in tools if not (have_facts and t.name == "get_history")]
        llm = model.bind_tools(offered) if state.get("tool_rounds", 0) < MAX_TOOL_ROUNDS else model
        system = SystemMessage(system_prompt() + _date_line(today) + _guidance(state.get("sources", [])) + _graph_section(state.get("graph_facts")))
        messages = [system] + _answered(state["messages"])
        reply = llm.invoke(messages)
        # an empty reply with tools on offer: the same prompt with none bound gets an answer, as on the last round
        if offered and llm is not model and not reply.tool_calls and not str(reply.content).strip():
            reply = model.invoke(messages)
        return {"messages": [reply]}

    # each result carries when its tool started and how long it took, for the chat's tool-call box. it rides in
    # additional_kwargs, which the ollama client doesn't send, so the model sees exactly what it saw before
    def run_tools(state: ChatState) -> dict:
        messages = []
        for tc in state["messages"][-1].tool_calls:
            started, t0 = time.time(), time.perf_counter()
            content = _run_tool(by_name, tc)
            timing = {"started_at": round(started * 1000), "ms": round((time.perf_counter() - t0) * 1000)}
            messages.append(ToolMessage(content, tool_call_id=tc["id"], name=tc["name"], additional_kwargs=timing))
        return {"messages": messages, "tool_rounds": state.get("tool_rounds", 0) + 1}

    def route(state: ChatState) -> str:
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else "finish"

    # small models drop the closing line on some answers, and this one isn't optional. the same id replaces the
    # message, so history and the eval see what the user saw. an empty answer stays empty, for the caller to report
    def finish(state: ChatState) -> Optional[dict]:
        last = state["messages"][-1]
        text = last.content if isinstance(last.content, str) else ""
        if not text.strip() or DISCLAIMER_MARK in text.lower():
            return None
        return {"messages": [AIMessage(f"{text.rstrip()}\n\n{DISCLAIMER}", id=last.id)]}

    graph = StateGraph(ChatState)
    graph.add_node("retrieve", retrieve_guidance)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "retrieve")
    graph.add_node("graph_retrieve", graph_node)
    graph.add_edge("retrieve", "graph_retrieve")
    graph.add_edge("graph_retrieve", "agent")
    graph.add_node("finish", finish)
    graph.add_conditional_edges("agent", route, ["tools", "finish"])
    graph.add_edge("tools", "agent")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)
