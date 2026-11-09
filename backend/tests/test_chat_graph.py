import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import ToolException, tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from app import config
from datetime import date

from app.agents.chat_graph import DISCLAIMER, MAX_TOOL_ROUNDS, SYSTEM_PROMPT, build_chat_graph
from tests.fakes import ScriptedChatModel, ollama_has, tool_call


# the model can't know the answer without calling it ("Ada" -> 24)
@tool
def get_lucky_number(name: str) -> int:
    """Return the lucky number for a person's name."""
    return len(name) * 7 + 3


@tool
def bad(name: str) -> int:
    """Rejects its argument the way a real tool does."""
    raise ToolException("bad arg")


@tool
def boom(name: str) -> int:
    """Fails like a database error that carries user data."""
    raise RuntimeError("secret user data 4321")


class AccountRow(BaseModel):
    balance: int


@tool
def strict(name: str) -> int:
    """Builds a model from a database value, which can fail validation."""
    AccountRow(balance="Jane Doe's Checking")
    return 1


TOOLS = [get_lucky_number, bad, boom, strict]
CONFIG = {"configurable": {"thread_id": "t1"}}


def test_chat_binds_seven_tools_and_the_prompt_names_both_forecast_tools(db, tmp_path):
    from app.agents.tools import make_tools
    from app.privacy.gateway import PrivacyGateway
    from tests.factories import add_user
    from tests.fakes import FakeKB
    with db() as s:
        add_user(s, "u1", "u1@x")
        s.commit()
        tools = make_tools(PrivacyGateway(s, "u1", audit_path=tmp_path / "audit.jsonl"), FakeKB())
        m = ScriptedChatModel(replies=[AIMessage("No subscriptions yet.")])
        run(m, tools=tools)
    assert len(m.tools_seen[0]) == 7 and {"get_recurring", "get_cash_forecast"} <= set(m.tools_seen[0])
    assert "get_recurring" in m.seen[0][0].content and "get_cash_forecast" in m.seen[0][0].content


def test_get_recurring_returns_scrubbed_rows(db, tmp_path):
    import json
    from app.agents.tools import make_tools
    from app.privacy.gateway import PrivacyGateway
    from tests.test_recurring_api import seed, stream
    from tests.fakes import FakeKB
    seed(db)
    with db() as s:
        stream(s, "Gym 12345")
        stream(s, "Secret Employer", direction="in")
        s.commit()
        tools = make_tools(PrivacyGateway(s, "dev", audit_path=tmp_path / "audit.jsonl"), FakeKB())
        m = ScriptedChatModel(replies=[tool_call("get_recurring"), AIMessage("You have recurring charges.")])
        run(m, tools=tools)
    text = m.seen[1][-1].content
    assert "Gym [number]" in text and "12345" not in text and "Secret Employer" not in text
    assert json.loads(text)["purpose"] == "recurring"


GUIDE = {"title": "How to reduce your debt", "section": "Highest interest rate method",
         "source_url": "https://www.consumerfinance.gov/about-us/blog/how-reduce-your-debt/",
         "text": "Pay the debt with the highest interest rate first."}


def run(model, question="q", tools=TOOLS, saver=None, retrieve=None, **state):
    graph = build_chat_graph(tools, model=model, checkpointer=saver or MemorySaver(), retrieve=retrieve)
    graph.invoke({"messages": [HumanMessage(question)], "tool_rounds": 0, **state}, CONFIG)
    return graph


def final(graph):
    return graph.get_state(CONFIG).values["messages"][-1]


def test_no_tool_call_returns_answer():
    model = ScriptedChatModel(replies=[AIMessage("hi there")])

    graph = run(model)

    assert final(graph).content == f"hi there\n\n{DISCLAIMER}"
    assert isinstance(model.seen[0][0], SystemMessage) and model.seen[0][0].content == SYSTEM_PROMPT
    assert not any(isinstance(m, SystemMessage) for m in graph.get_state(CONFIG).values["messages"])


def test_tool_call_round_trips():
    model = ScriptedChatModel(replies=[tool_call("get_lucky_number", {"name": "Ada"}), AIMessage("It's 24.")])

    graph = run(model)

    sent = model.seen[1][-1]
    assert isinstance(sent, ToolMessage)
    assert (sent.tool_call_id, sent.content) == ("c1", "24")
    assert final(graph).content == f"It's 24.\n\n{DISCLAIMER}"


# each result records when its tool started and how long it ran, for the chat's tool box; the content is unchanged
def test_tool_result_records_its_timing():
    model = ScriptedChatModel(replies=[tool_call("get_lucky_number", {"name": "Ada"}), AIMessage("It's 24.")])

    run(model)

    sent = model.seen[1][-1]
    assert sent.content == "24"
    assert set(sent.additional_kwargs) == {"started_at", "ms"}
    assert all(isinstance(v, int) and v >= 0 for v in sent.additional_kwargs.values())


# small models invent tool names; that reaches the model as an error, not a crash
def test_unknown_tool_gets_error_message():
    model = ScriptedChatModel(replies=[tool_call("nope"), AIMessage("ok")])

    run(model)

    assert model.seen[1][-1].content.startswith("error: unknown tool")


# a ToolException is the tool talking to the model ("bad category"), so its text is forwarded
def test_tool_exception_text_reaches_model():
    model = ScriptedChatModel(replies=[tool_call("bad", {"name": "x"}), AIMessage("ok")])

    run(model)

    assert model.seen[1][-1].content == "error: bad arg"


# any other exception text can carry user data, so only its type reaches the model
def test_generic_exception_text_not_forwarded():
    model = ScriptedChatModel(replies=[tool_call("boom", {"name": "x"}), AIMessage("ok")])

    run(model)

    assert model.seen[1][-1].content == "error: boom failed (RuntimeError)"
    assert not any("4321" in str(m.content) for call in model.seen for m in call)


# a ValidationError raised inside a tool prints input_value, which can be user data from the db
def test_validation_error_input_not_forwarded():
    model = ScriptedChatModel(replies=[tool_call("strict", {"name": "x"}), AIMessage("ok")])

    run(model)

    sent = model.seen[1][-1].content
    assert sent.startswith("error:")
    assert "Jane" not in sent


# ollama going away is not something to paper over with a fake answer
def test_model_error_propagates():
    model = ScriptedChatModel(replies=[ConnectionError("down")])

    with pytest.raises(ConnectionError):
        run(model)


# a model that never stops calling tools still gives the user an answer
def test_round_cap_then_answer_without_tools():
    calls = [tool_call("get_lucky_number", {"name": "Ada"}, id=f"c{i}") for i in range(MAX_TOOL_ROUNDS)]
    model = ScriptedChatModel(replies=calls + [AIMessage("best answer I have")])

    graph = run(model)

    assert MAX_TOOL_ROUNDS == 6
    assert len(model.seen) == 7
    assert all(t is not None for t in model.tools_seen[:6])
    assert model.tools_seen[6] is None
    assert final(graph).content == f"best answer I have\n\n{DISCLAIMER}"


def test_tool_rounds_reset_each_turn():
    saver = MemorySaver()
    model = ScriptedChatModel(replies=[tool_call("get_lucky_number", {"name": "A"}, id="a"),
                                       tool_call("get_lucky_number", {"name": "B"}, id="b"),
                                       AIMessage("turn one"), AIMessage("turn two")])
    graph = run(model, saver=saver)

    graph.invoke({"messages": [HumanMessage("again")], "tool_rounds": 0}, CONFIG)

    assert model.tools_seen[-1] is not None


def test_memory_across_turns():
    saver = MemorySaver()
    model = ScriptedChatModel(replies=[AIMessage("first answer"), AIMessage("second answer")])
    graph = run(model, question="first question", saver=saver)

    graph.invoke({"messages": [HumanMessage("second question")], "tool_rounds": 0}, CONFIG)

    contents = [m.content for m in model.seen[-1]]
    assert "first question" in contents and f"first answer\n\n{DISCLAIMER}" in contents


def test_pseudonym_kept_in_state():
    saver = MemorySaver()
    model = ScriptedChatModel(replies=[AIMessage("a"), AIMessage("b")])
    graph = run(model, saver=saver, pseudonym="user_ab12")

    graph.invoke({"messages": [HumanMessage("again")], "tool_rounds": 0}, CONFIG)

    assert graph.get_state(CONFIG).values["pseudonym"] == "user_ab12"


# retrieved guidance rides in the system prompt only, so old threads never carry stale guidance
def test_retrieve_puts_guidance_in_system_prompt_not_messages():
    model = ScriptedChatModel(replies=[AIMessage("pay the card")])

    graph = run(model, question="which debt first?", retrieve=lambda q: [GUIDE])

    system = model.seen[0][0].content
    assert system.startswith(SYSTEM_PROMPT)
    assert "Guidance retrieved for this question:" in system
    assert "- How to reduce your debt / Highest interest rate method: Pay the debt with the highest" in system
    assert "consumerfinance.gov" not in system   # urls are the app's job, not the model's
    stored = graph.get_state(CONFIG).values
    assert not any("Guidance retrieved" in str(m.content) for m in stored["messages"])
    assert stored["sources"] == [GUIDE]


def test_retrieve_searches_each_new_question():
    asked = []
    model = ScriptedChatModel(replies=[AIMessage("a"), AIMessage("b")])
    graph = run(model, question="first?", retrieve=lambda q: asked.append(q) or [GUIDE])

    graph.invoke({"messages": [HumanMessage("second?")], "tool_rounds": 0}, CONFIG)

    assert asked == ["first?", "second?"]


# without the date the model guessed: "last month" searched february 2023 and answered $0
def test_today_goes_in_the_system_prompt_and_is_read_on_every_call():
    model = ScriptedChatModel(replies=[AIMessage("a"), AIMessage("b")])
    days = iter([date(2026, 10, 20), date(2026, 10, 21)])
    graph = build_chat_graph(TOOLS, model=model, checkpointer=MemorySaver(), retrieve=lambda q: [GUIDE],
                             today=lambda: next(days))

    graph.invoke({"messages": [HumanMessage("q")], "tool_rounds": 0}, CONFIG)
    graph.invoke({"messages": [HumanMessage("q2")]}, CONFIG)

    first, second = model.seen[0][0].content, model.seen[1][0].content
    assert first.startswith(SYSTEM_PROMPT) and "Today's date is 2026-10-20." in first
    assert first.index("Today's date") < first.index("Guidance retrieved")
    # a thread picked up later gets the new date, and none of it is stored
    assert "Today's date is 2026-10-21." in second and "2026-10-20" not in second
    assert not any(isinstance(m, SystemMessage) for m in graph.get_state(CONFIG).values["messages"])


# a small model read "last month" as the last 30 days, so the calendar month is spelled out, across a year too
@pytest.mark.parametrize("today, this_month, last_from, last_to", [
    (date(2026, 10, 8), "2026-10-01", "2026-09-01", "2026-09-30"),
    (date(2027, 1, 31), "2027-01-01", "2026-12-01", "2026-12-31"),
    (date(2028, 3, 1), "2028-03-01", "2028-02-01", "2028-02-29"),
])
def test_date_line_spells_out_the_calendar_months(today, this_month, last_from, last_to):
    model = ScriptedChatModel(replies=[AIMessage("a")])
    graph = build_chat_graph(TOOLS, model=model, checkpointer=MemorySaver(), today=lambda: today)

    graph.invoke({"messages": [HumanMessage("q")], "tool_rounds": 0}, CONFIG)

    system = model.seen[0][0].content
    assert f"This month means {this_month} to {today.isoformat()}." in system
    assert f"Last month means {last_from} to {last_to}." in system


def test_no_guidance_when_nothing_retrieved():
    model = ScriptedChatModel(replies=[AIMessage("a")])

    run(model, retrieve=lambda q: [])

    assert model.seen[0][0].content == SYSTEM_PROMPT


# history needs each turn's sources, keyed by the question that started the turn
def test_sources_by_turn_keyed_by_question_id():
    model = ScriptedChatModel(replies=[AIMessage("a"), AIMessage("b")])
    other = {**GUIDE, "title": "Other"}
    found = iter([[GUIDE], [other]])
    graph = run(model, question="first?", retrieve=lambda q: next(found))

    graph.invoke({"messages": [HumanMessage("second?")], "tool_rounds": 0}, CONFIG)

    values = graph.get_state(CONFIG).values
    questions = [m for m in values["messages"] if isinstance(m, HumanMessage)]
    slim = lambda g: [{k: g[k] for k in ("title", "section", "source_url")}]
    assert values["sources_by_turn"] == {questions[0].id: slim(GUIDE), questions[1].id: slim(other)}


# review on #9: the reset belongs to the graph, so a caller that doesn't pass tool_rounds still gets tools
def test_tool_rounds_reset_without_the_caller():
    saver = MemorySaver()
    calls = [tool_call("get_lucky_number", {"name": "A"}, id=f"c{i}") for i in range(MAX_TOOL_ROUNDS)]
    model = ScriptedChatModel(replies=calls + [AIMessage("turn one"), AIMessage("turn two")])
    graph = run(model, saver=saver)

    graph.invoke({"messages": [HumanMessage("again")]}, CONFIG)

    assert model.tools_seen[-1] is not None


# review on #9: a turn cut off mid-tool leaves calls with no results; the model must never see them unanswered
def test_dangling_tool_calls_answered_before_the_model_sees_them():
    model = ScriptedChatModel(replies=[AIMessage("answer")])
    cut_off = tool_call("get_lucky_number", {"name": "Ada"}, id="lost")

    run(model, question="second question", messages=[HumanMessage("first"), cut_off, HumanMessage("second question")])

    sent = model.seen[0]
    i = next(n for n, m in enumerate(sent) if getattr(m, "tool_calls", None))
    assert isinstance(sent[i + 1], ToolMessage) and sent[i + 1].tool_call_id == "lost"
    assert sent[i + 1].content.startswith("error:")


# text next to a tool call isn't sent back: qwen wrote it in thai once, then answered in thai (2026-11-08).
# the checkpoint keeps it as written; only what the model is sent changes
def test_text_next_to_a_tool_call_not_sent_back_to_the_model():
    call = tool_call("get_lucky_number", {"name": "Ada"})
    call.content = "ฉันจะตรวจสอบ"
    model = ScriptedChatModel(replies=[call, AIMessage("It's 24.")])

    graph = run(model)

    sent = next(m for m in model.seen[1] if getattr(m, "tool_calls", None))
    assert sent.content == "" and sent.tool_calls == call.tool_calls
    stored = next(m for m in graph.get_state(CONFIG).values["messages"] if getattr(m, "tool_calls", None))
    assert stored.content == "ฉันจะตรวจสอบ"


# a real tool call on the local model, end to end through the graph
@pytest.mark.skipif(not ollama_has(config.OLLAMA_MODEL), reason="needs ollama running with OLLAMA_MODEL pulled")
def test_live_tool_call_round_trip():
    graph = build_chat_graph([get_lucky_number], checkpointer=MemorySaver())

    graph.invoke({"messages": [HumanMessage("What is Ada's lucky number? Use the tool.")], "tool_rounds": 0},
                 CONFIG)

    assert "24" in final(graph).content


# tpc-48: the model asked for the calendar month when the question said the last 30 days
def test_date_line_spells_out_the_last_30_days():
    from app.agents.chat_graph import _date_line
    assert "The last 30 days means 2026-10-07 to 2026-11-06." in _date_line(lambda: date(2026, 11, 6))


# tpc-47: with debt guidance in the prompt, the model answered from it and skipped the lookup
def test_prompt_says_personal_questions_need_the_lookup_despite_guidance():
    assert "needs the lookup even when the guidance below covers the topic" in SYSTEM_PROMPT
    assert "name the debt with the highest APR" in SYSTEM_PROMPT and "the user has no debts" in SYSTEM_PROMPT


# code adds the closing line when the model drops it, once, and never to an empty answer
@pytest.mark.parametrize("reply, expected", [
    ("Pay the card.", f"Pay the card.\n\n{DISCLAIMER}"),
    (f"Pay the card.\n\n{DISCLAIMER}", f"Pay the card.\n\n{DISCLAIMER}"),
    ("Pay the card. This is NOT financial advice.", "Pay the card. This is NOT financial advice."),
    ("", ""),
])
def test_final_answer_always_carries_the_disclaimer(reply, expected):
    state = build_chat_graph([], model=ScriptedChatModel(replies=[AIMessage(reply)])).invoke(
        {"messages": [HumanMessage("hi")], "tool_rounds": 0})
    assert state["messages"][-1].content == expected
    assert len(state["messages"]) == 2      # replaced, not added as a second answer


# tpc-27: a condition with no vector guidance can't reach it through a tool or be told it's there
@pytest.mark.parametrize("mode, guidance, search", [
    ("vector", True, True), ("hybrid", True, True), ("hybrid-shuffled", True, True),
    ("graph", False, False), ("none", False, False),
])
def test_prompt_follows_the_retrieval_mode(monkeypatch, mode, guidance, search):
    from app.agents.chat_graph import system_prompt
    monkeypatch.setattr(config, "KG_CHAT", mode)
    prompt = system_prompt()
    assert ("guidance below" in prompt) is guidance
    assert ("search_kb" in prompt) is search


def test_vector_prompt_is_todays_prompt_byte_for_byte(monkeypatch):
    from app.agents.chat_graph import system_prompt
    monkeypatch.setattr(config, "KG_CHAT", "vector")
    assert system_prompt() == SYSTEM_PROMPT


@pytest.mark.parametrize("mode, retrieved", [("vector", True), ("hybrid", True), ("none", False), ("graph", False)])
def test_retrieval_only_runs_in_vector_modes(monkeypatch, mode, retrieved):
    monkeypatch.setattr(config, "KG_CHAT", mode)
    calls = []
    graph = build_chat_graph([], retrieve=lambda q: calls.append(q) or [], model=ScriptedChatModel(replies=[AIMessage("x")]))
    graph.invoke({"messages": [HumanMessage("hi")], "tool_rounds": 0})
    assert bool(calls) is retrieved


def test_an_empty_reply_with_tools_gets_one_more_try_without_them():
    @tool
    def t() -> str:
        "A tool."
        return "x"
    model = ScriptedChatModel(replies=[AIMessage(""), AIMessage("Answer.")])
    state = build_chat_graph([t], model=model).invoke({"messages": [HumanMessage("hi")], "tool_rounds": 0})
    assert state["messages"][-1].content.startswith("Answer.")
    assert model.tools_seen == [["t"], None]
