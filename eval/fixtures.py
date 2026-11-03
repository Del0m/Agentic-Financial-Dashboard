import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Union, get_args

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents.budget_llm import Action, Diagnosis
from app.agents.tools import PFC_CATEGORIES, VALID_CATEGORIES
from app.budget_engine.goals import GOAL_KINDS
from app.budget_engine.signals import RANK
from app.db.base import Base
from app.db.models import Liability
from eval.budget_users import profile_by_id
from tests.factories import add_account, add_txn, add_user

# sqlite doesn't enforce the enum, so a typo like "credit card" would be stored as-is
LIABILITY_KINDS = tuple(Liability.__table__.c.kind.type.enums)
KINDS = ("chat", "budget", "goal")
CAUSES = get_args(Diagnosis.model_fields["cause"].annotation)
OVERSPEND_ACTIONS = get_args(Action.model_fields["action"].annotation)
SIGNAL_KINDS = tuple(RANK)
# the goal graph's finished endings; "error" and "busy" are never a golden answer
GOAL_STATUSES = ("ok", "no_surplus", "nothing_offered", "no_affordable_goals")

# scenario files -> validated dataclasses -> rows in a throwaway db (spec: tpc-16 scenario format, tpc-26 kinds).
# every problem names the file and field, so a typo fails loudly instead of scoring a silent 0


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    text: Optional[str] = None
    number: Optional[float] = None
    tol: float = 0.0

    @property
    def label(self) -> str:
        return self.text if self.text is not None else f"{self.number:g}±{self.tol:g}"


@dataclass(frozen=True)
class Expect:
    facts: list[Fact]
    forbid: list[str]
    tools: list[str]
    sources: list[str]
    abstain: bool = False       # passes only by saying the data isn't there, with no dollar amount (tpc-26)


@dataclass(frozen=True)
class Scenario:
    id: str
    question: str
    user: dict
    golden_answer: str
    expect: Expect
    path: Path
    tags: tuple[str, ...] = ()
    kind: str = "chat"


@dataclass(frozen=True)
class ActionRule:
    accept: tuple[str, ...]
    ban: tuple[str, ...] = ()


# every field is checked only when the scenario sets it. flags are exact: an extra one is a false alarm, an empty
# list makes the scenario a control, and leaving them out suits a scenario about a signal, not about overspending
@dataclass(frozen=True)
class BudgetExpect:
    flags: Optional[tuple[str, ...]]
    causes: dict[str, str]
    actions: dict[str, ActionRule]
    flag_by: dict[str, date]
    alert_kinds: tuple[str, ...]
    message_facts: list[Fact]
    min_days_ahead: Optional[int] = None


# seeded through `start` with no run, then one real daily run per day through `end`
@dataclass(frozen=True)
class BudgetScenario:
    id: str
    profile: str
    start: date
    end: date
    golden_answer: str
    expect: BudgetExpect
    path: Path
    tags: tuple[str, ...] = ()
    kind: str = "budget"

    @property
    def is_control(self) -> bool:
        return self.expect.flags == ()


@dataclass(frozen=True)
class GoalExpect:
    status: str
    required_kinds: tuple[str, ...]
    banned_kinds: tuple[str, ...]
    first_kind: Optional[str] = None


@dataclass(frozen=True)
class GoalScenario:
    id: str
    profile: str
    today: date
    prefs: Optional[dict]
    golden_answer: str
    expect: GoalExpect
    path: Path
    tags: tuple[str, ...] = ()
    kind: str = "goal"


AnyScenario = Union[Scenario, BudgetScenario, GoalScenario]


def _fail(path: Path, field: str, problem: str):
    raise ScenarioError(f"{path.name}: {field}: {problem}")


def _require(path: Path, d: dict, key: str, kind, where: str = ""):
    field = f"{where}{key}"
    if key not in d:
        _fail(path, field, "missing")
    if not isinstance(d[key], kind) or (isinstance(d[key], str) and not d[key].strip()):
        _fail(path, field, "wrong type or empty")
    return d[key]


def _fact(path: Path, i: int, raw) -> Fact:
    field = f"expect.facts[{i}]"
    if not isinstance(raw, dict) or ("text" in raw) == ("number" in raw):
        _fail(path, field, "needs exactly one of text or number")
    if "text" in raw:
        # an empty text fact is a substring of every answer, so it would always pass
        if not isinstance(raw["text"], str) or not raw["text"].strip():
            _fail(path, field, "text must be a non-empty string")
        return Fact(text=raw["text"])
    try:
        return Fact(number=float(raw["number"]), tol=float(raw.get("tol", 0)))
    except (TypeError, ValueError):
        _fail(path, field, "number and tol must be numbers")


# a bare string would become a list of characters, and "" matches every answer
def _strings(path: Path, exp: dict, key: str) -> list[str]:
    value = exp.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        _fail(path, key if key == "tags" else f"expect.{key}", "must be a list of non-empty strings")
    return value


# every field seed() reads, so a typo fails the load (exit 2) instead of every run scoring a silent 0
def _check_liability(path: Path, raw: dict, where: str) -> None:
    if _require(path, raw, "kind", str, where) not in LIABILITY_KINDS:
        _fail(path, where + "kind", f"{raw['kind']!r} is not one of {', '.join(LIABILITY_KINDS)}")
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if raw.get("apr") is not None and not number(raw["apr"]):
        _fail(path, where + "apr", "must be a number or null")
    if "min_payment" in raw and not number(raw["min_payment"]):
        _fail(path, where + "min_payment", "must be a number")
    if "due_in_days" in raw and (not isinstance(raw["due_in_days"], int) or isinstance(raw["due_in_days"], bool)
                                 or raw["due_in_days"] < 0):
        _fail(path, where + "due_in_days", "must be a whole number, 0 or more")


def _check_user(path: Path, user: dict) -> None:
    accounts = _require(path, user, "accounts", list, "user.")
    names = set()
    for i, a in enumerate(accounts):
        where = f"user.accounts[{i}]."
        name = _require(path, a, "name", str, where)
        # the name is the account's plaid_account_id in seed(), so two with one name can't both be stored
        if name in names:
            _fail(path, where + "name", f"{name!r} is used by another account")
        names.add(name)
        _require(path, a, "type", str, where)
        _require(path, a, "balance", (int, float), where)
        if "liability" in a:
            _check_liability(path, _require(path, a, "liability", dict, where), where + "liability.")
    for i, t in enumerate(user.get("transactions", [])):
        where = f"user.transactions[{i}]."
        if _require(path, t, "account", str, where) not in names:
            _fail(path, where + "account", f"no account named {t['account']!r}")
        if _require(path, t, "category", str, where) not in PFC_CATEGORIES:
            _fail(path, where + "category", f"{t['category']!r} is not a Plaid primary category")
        if _require(path, t, "days_ago", int, where) < 0:
            _fail(path, where + "days_ago", "must be 0 or more")
        _require(path, t, "amount", (int, float), where)


def _date(path: Path, d: dict, key: str, where: str = "") -> date:
    raw = _require(path, d, key, str, where)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        _fail(path, where + key, f"{raw!r} is not a YYYY-MM-DD date")


def _choices(path: Path, field: str, values: list[str], allowed) -> tuple[str, ...]:
    for v in values:
        if v not in allowed:
            _fail(path, field, f"{v!r} is not one of {', '.join(allowed)}")
    return tuple(values)


def _profile(path: Path, d: dict) -> str:
    pid = _require(path, d, "profile", str)
    if profile_by_id(pid) is None:
        _fail(path, "profile", f"no synthetic user {pid!r} in eval/budget_users.py")
    return pid


def _load_chat(path: Path, d: dict, sid: str, golden: str, tags: tuple[str, ...]) -> Scenario:
    question = _require(path, d, "question", str)
    user = _require(path, d, "user", dict)
    _check_user(path, user)
    exp = _require(path, d, "expect", dict)
    abstain = exp.get("abstain", False)
    if not isinstance(abstain, bool):
        _fail(path, "expect.abstain", "must be true or false")
    # an abstain scenario has no fact to find: the answer should have none
    if abstain and exp.get("facts"):
        _fail(path, "expect.facts", "must be left out of an abstain scenario")
    facts = [] if abstain else _require(path, exp, "facts", list, "expect.")
    if not abstain and not facts:
        _fail(path, "expect.facts", "must not be empty")
    expect = Expect(facts=[_fact(path, i, f) for i, f in enumerate(facts)], forbid=_strings(path, exp, "forbid"),
                    tools=_strings(path, exp, "tools"), sources=_strings(path, exp, "sources"), abstain=abstain)
    return Scenario(id=sid, question=question, user=user, golden_answer=golden, expect=expect, path=path, tags=tags)


def _optional_dict(path: Path, exp: dict, key: str) -> dict:
    return _require(path, exp, key, dict, "expect.") if key in exp else {}


# a flag, cause or action on a category nobody spelled right would never match, and score as an agent failure
def _categories(path: Path, field: str, values) -> None:
    for cat in values:
        if cat not in VALID_CATEGORIES:
            _fail(path, field, f"{cat!r} is not a Plaid primary category")


def _action_rules(path: Path, exp: dict) -> dict[str, ActionRule]:
    rules = {}
    for cat, raw in _optional_dict(path, exp, "actions").items():
        where = f"expect.actions.{cat}."
        if not isinstance(raw, dict):
            _fail(path, where[:-1], "must be an object with accept and ban")
        accept = _choices(path, where + "accept", _strings(path, raw, "accept"), OVERSPEND_ACTIONS)
        if not accept:
            _fail(path, where + "accept", "must not be empty")
        rules[cat] = ActionRule(accept, _choices(path, where + "ban", _strings(path, raw, "ban"), OVERSPEND_ACTIONS))
    return rules


def _load_budget(path: Path, d: dict, sid: str, golden: str, tags: tuple[str, ...]) -> BudgetScenario:
    profile = _profile(path, d)
    start, end = _date(path, d, "start"), _date(path, d, "end")
    if end <= start:
        _fail(path, "end", "must be after start: each day after start is one run")
    exp = _require(path, d, "expect", dict)
    flags = _require(path, exp, "flags", list, "expect.") if "flags" in exp else None
    causes = _optional_dict(path, exp, "causes")
    flag_by = {cat: _date(path, exp["flag_by"], cat, "expect.flag_by.") for cat in _optional_dict(path, exp, "flag_by")}
    actions = _action_rules(path, exp)
    for field, cats in (("flags", flags or ()), ("causes", causes), ("actions", actions), ("flag_by", flag_by)):
        _categories(path, f"expect.{field}", cats)
    _choices(path, "expect.causes", list(causes.values()), CAUSES)
    alert_kinds = _choices(path, "expect.alert_kinds", _strings(path, exp, "alert_kinds"), SIGNAL_KINDS)
    days = exp.get("min_days_ahead")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or days < 0):
        _fail(path, "expect.min_days_ahead", "must be a whole number, 0 or more")
    raw_facts = _require(path, exp, "message_facts", list, "expect.") if "message_facts" in exp else []
    facts = [_fact(path, i, f) for i, f in enumerate(raw_facts)]
    return BudgetScenario(id=sid, profile=profile, start=start, end=end, golden_answer=golden, path=path, tags=tags,
                          expect=BudgetExpect(None if flags is None else tuple(flags), causes, actions, flag_by,
                                              alert_kinds, facts, days))


def _load_goal(path: Path, d: dict, sid: str, golden: str, tags: tuple[str, ...]) -> GoalScenario:
    profile = _profile(path, d)
    today = _date(path, d, "today")
    prefs = d.get("prefs")
    if prefs is not None and not isinstance(prefs, dict):
        _fail(path, "prefs", "must be an object")
    exp = _require(path, d, "expect", dict)
    status = _require(path, exp, "status", str, "expect.")
    _choices(path, "expect.status", [status], GOAL_STATUSES)
    first = exp.get("first_kind")
    if first is not None:
        _choices(path, "expect.first_kind", [first], GOAL_KINDS)
    expect = GoalExpect(status, _choices(path, "expect.required_kinds", _strings(path, exp, "required_kinds"), GOAL_KINDS),
                        _choices(path, "expect.banned_kinds", _strings(path, exp, "banned_kinds"), GOAL_KINDS), first)
    return GoalScenario(id=sid, profile=profile, today=today, prefs=prefs, golden_answer=golden, expect=expect,
                        path=path, tags=tags)


def load_scenario(path: Path) -> AnyScenario:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(path, "json", str(exc))
    if not isinstance(d, dict):
        _fail(path, "json", "top level must be an object")
    sid = _require(path, d, "id", str)
    if sid != path.stem:
        _fail(path, "id", f"{sid!r} must match the file name {path.stem!r}")
    kind = d.get("kind", "chat")
    if kind not in KINDS:
        _fail(path, "kind", f"{kind!r} is not one of {', '.join(KINDS)}")
    golden = _require(path, d, "golden_answer", str)
    tags = tuple(_strings(path, d, "tags"))
    loader = {"chat": _load_chat, "budget": _load_budget, "goal": _load_goal}[kind]
    return loader(path, d, sid, golden, tags)


# ids equal file stems, so one folder can't hold two scenarios with the same id
def load_scenarios(directory: Path, only: Optional[str] = None) -> list[AnyScenario]:
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.json"))]
    if only is not None:
        scenarios = [s for s in scenarios if s.id == only]
        if not scenarios:
            raise ScenarioError(f"no scenario with id {only!r} in {directory}")
    return scenarios


# same setup as the db fixture in tests/conftest.py: one in-memory db shared by every session
@contextmanager
def temp_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _cents(dollars: float) -> int:
    return round(dollars * 100)


def seed(session: Session, scenario: Scenario, today: date) -> str:
    user_id = f"eval-{scenario.id}"
    item = add_user(session, user_id, f"{scenario.id}@eval.local")
    accounts = {}
    for a in scenario.user["accounts"]:
        liability = None
        if "liability" in a:
            raw = a["liability"]
            liability = {"kind": raw["kind"], "apr": raw.get("apr")}
            if "min_payment" in raw:
                liability["min_payment_cents"] = _cents(raw["min_payment"])
            if "due_in_days" in raw:
                liability["next_due_date"] = today + timedelta(days=raw["due_in_days"])
        accounts[a["name"]] = add_account(session, item, a["name"], type_=a["type"], subtype=a.get("subtype"),
                                          balance=_cents(a["balance"]), liability=liability)
    for t in scenario.user.get("transactions", []):
        add_txn(session, accounts[t["account"]], _cents(t["amount"]), t["category"],
                merchant=t.get("merchant", "Shop"), day=today - timedelta(days=t["days_ago"]))
    session.commit()
    if "get_recurring" in scenario.expect.tools:
        from app.budget_service.recurring import refresh_streams
        refresh_streams(session, user_id, today, None)
        session.commit()
    return user_id
