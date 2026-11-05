"""What the graph says about a chat question (tpc-39, architecture 6b.4). No model: entity linking and time are by rule.

Facts about the user are written with a scrub function, because they reach a prompt through the privacy gateway.
"""
import json
import random
import re
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.budget_engine.txns import add_months, month_end, month_start
from app.db.models import KGEdge, KGNode

from . import query

MAX_FACTS = 25
MAX_PRICE_STEPS = 4     # more changes than this isn't a price, it's a varying bill
SHUFFLE_SEED = 27       # the hybrid-shuffled control: same facts and length, with values that no longer belong to them
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
          "december")
# words too common to link on. a merchant or advice links on its other words: "gym" finds FitClub Gym
STOP_WORDS = frozenset("""a about after all am an and any are at back be been budget but by can co cut cutting did do does
for from gone had has have how i if in inc is it its last limit me month much my next no not of on or our out over
price shop so spend spending store than that the them then there this to try up was what when where which who why
will with would year you your""".split())
HISTORY_RELATIONS = ("SPENDS_AT", "SUBSCRIBES", "BUDGETS", "ABOUT")


@dataclass(frozen=True)
class Fact:
    rel: str
    subject: str
    value: str

    @property
    def line(self) -> str:
        return f"{self.subject}: {self.value}"


@dataclass(frozen=True)
class Gathered:
    as_of: date
    user: tuple[Fact, ...]
    guidance: tuple[Fact, ...]


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", re.sub(r"['’]s\b", "", text.casefold())))


def _money(cents: Optional[int]) -> str:
    return "unknown" if cents is None else f"${cents / 100:,.2f}"


# the period a question is about, as its last day and its first, both inclusive. a period that's still running ends
# today, and one wholly in the future keeps its own dates, so a limit question about 2030 doesn't get this year's.
# "since" runs to today. a question naming no period has no first day: everything up to today
def read_time(question: str, today: date) -> tuple[date, Optional[date]]:
    text = question.casefold()
    exact = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if exact:
        try:
            day = date.fromisoformat(exact.group())
            return day, day
        except ValueError:
            pass

    def period(first: date, last: date, since: bool) -> tuple[date, date]:
        return (today if since or first <= today <= last else last), first

    months = "|".join(MONTHS)
    year = re.search(r"\b(?:19|20)\d{2}\b", text)
    named = re.search(rf"\b(in|since|during) ({months})\b|\b({months}) (?:19|20)\d{{2}}\b", text)
    if named:
        name = named.group(2) or named.group(3)
        first = date(int(year.group()) if year else today.year, MONTHS.index(name) + 1, 1)
        if not year and first > today:
            first = date(today.year - 1, first.month, 1)
        return period(first, month_end(first), named.group(1) == "since")
    if year:
        first = date(int(year.group()), 1, 1)
        return period(first, date(first.year, 12, 31), f"since {year.group()}" in text)
    if "last year" in text:
        first = date(today.year - 1, 1, 1)
        return period(first, date(first.year, 12, 31), "since last year" in text)
    if "last month" in text:
        first = add_months(month_start(today), -1)
        return period(first, month_end(first), "since last month" in text)
    if "this year" in text:
        return today, date(today.year, 1, 1)
    if "this month" in text:
        return today, month_start(today)
    return today, None


@lru_cache(maxsize=4)
def _aliases(path: Path) -> tuple[tuple[str, str], ...]:
    if not path.exists():
        return ()
    pairs = []
    for c in json.loads(path.read_text(encoding="utf-8")):
        for name in (c["key"], c["label"], *c.get("aliases", ())):
            pairs.append((name.casefold(), c["key"]))
    return tuple(pairs)


def _linked_concepts(session: Session, question: str) -> list[KGNode]:
    text = question.casefold()
    keys = {key for name, key in _aliases(config.KB_DIR / "concepts.json")
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)}
    if not keys:
        return []
    return list(session.scalars(select(KGNode).where(KGNode.user_id.is_(None), KGNode.kind == "concept", KGNode.key.in_(keys))
                                .order_by(KGNode.key)))


def _nodes(session: Session, user_id: str, kind: str) -> list[KGNode]:
    return list(session.scalars(select(KGNode).where(KGNode.user_id == user_id, KGNode.kind == kind).order_by(KGNode.key)))


def _matches(label: Optional[str], words: set[str]) -> bool:
    return bool((_words(label or "") - STOP_WORDS) & words)


@dataclass(frozen=True)
class Window:
    end: date                # both inclusive: a fact counts if any of it falls in the period
    start: Optional[date]    # None means all history up to end


def _edges(session: Session, user_id: str, rel: str, window: Window, *, src_id: Optional[int] = None,
           dst_id: Optional[int] = None) -> list[KGEdge]:
    stmt = select(KGEdge).where(KGEdge.user_id == user_id, KGEdge.rel == rel, KGEdge.expired_at.is_(None),
                                KGEdge.valid_from <= window.end)
    if window.start is not None:
        stmt = stmt.where((KGEdge.valid_to.is_(None)) | (KGEdge.valid_to > window.start))
    if src_id is not None:
        stmt = stmt.where(KGEdge.src_id == src_id)
    if dst_id is not None:
        stmt = stmt.where(KGEdge.dst_id == dst_id)
    return list(session.scalars(stmt.order_by(KGEdge.valid_from, KGEdge.id)))


def _merchant_facts(session: Session, user_id: str, merchant: KGNode, scrub: Callable, window: Window) -> list[Fact]:
    name = scrub(merchant.label or merchant.key)
    months = [e for e in _edges(session, user_id, "SPENDS_AT", window, dst_id=merchant.id) if (e.value_json or {}).get("cents", 0) > 0]
    if not months:
        return []
    first, last = months[0], months[-1]
    # one charge a month is a price, so a change in it is worth stating: "when did it go up?"
    steps, price = [], None
    for e in months:
        if e.value_json.get("count") == 1 and e.value_json["cents"] != price:
            price = e.value_json["cents"]
            steps.append(f"{_money(price)} from {e.valid_from:%B %Y}")
    prices = f", priced {', then '.join(steps)}" if 1 < len(steps) <= MAX_PRICE_STEPS else ""
    # within a period, "first" only means first in that period, and a question about it usually wants the total
    scope = "in the period asked, " if window.start is not None else ""
    total = f", {_money(sum(e.value_json['cents'] for e in months))} in total" if window.start is not None else ""
    return [Fact("SPENDS_AT", f"Charges at {name}",
                 f"{scope}first in {first.valid_from:%B %Y}, latest in {last.valid_from:%B %Y}, in {len(months)} months{total}, "
                 f"{_money(last.value_json['cents'])} in the latest month{prices}")]


def _stream_facts(session: Session, user_id: str, words: set[str], scrub: Callable, window: Window) -> list[Fact]:
    out = []
    for stream in _nodes(session, user_id, "stream"):
        if stream.key.startswith("out|") and _matches(stream.label, words):
            edges = _edges(session, user_id, "SUBSCRIBES", window, dst_id=stream.id)
            if edges:
                parts = [f"{_money(e.value_json['typical_cents'])} from {e.valid_from:%B %Y}"
                         + ("" if e.valid_to is None else f" to {e.valid_to:%B %Y}") for e in edges]
                out.append(Fact("SUBSCRIBES", f"Recurring charge {scrub(stream.label)}", "; ".join(parts)))
    return out


def _category_label(category: KGNode) -> str:
    return (category.label or category.key.replace("_", " ")).lower()


def _budget_facts(session: Session, user_id: str, category: KGNode, window: Window) -> list[Fact]:
    edges = _edges(session, user_id, "BUDGETS", window, dst_id=category.id)
    if not edges:
        return []
    parts = [f"{_money(e.value_json['limit_cents'])} in {e.valid_from:%B %Y}" for e in edges]
    return [Fact("BUDGETS", f"{_category_label(category)} budget limit", ", ".join(parts))]


# an advice label is an alert's message, and a code-written one names the merchant as the bank gave it
def _advice_facts(session: Session, user_id: str, words: set[str], categories: list[KGNode], scrub: Callable,
                  window: Window) -> list[Fact]:
    category_ids = {c.id for c in categories}
    out = []
    for advice in _nodes(session, user_id, "advice"):
        edges = _edges(session, user_id, "ABOUT", window, src_id=advice.id)
        if edges and (_matches(advice.label, words) or edges[-1].dst_id in category_ids):
            value = edges[-1].value_json or {}
            outcome = value.get("outcome") or "no decision yet"
            out.append(Fact("ABOUT", f"Advice on {edges[-1].valid_from:%B %-d, %Y}",
                            f"\"{scrub(advice.label)}\" The user's decision: {outcome}."))
    return out


def _guidance_facts(session: Session, user_id: str, concepts: list[KGNode], on_date: date) -> list[Fact]:
    if not concepts:
        return []
    # two hops, because a Roth IRA's limit is on the IRA it's a kind of
    snapshot = query.neighborhood(session, user_id=user_id, node_ids=[c.id for c in concepts], on_date=on_date, hops=2,
                                  limit=MAX_FACTS * 4)
    labels = {n.id: n.label or n.key for n in snapshot.nodes}
    out = []
    for e in snapshot.edges:
        if e.user_id is not None or e.rel == "MENTIONED_IN":
            continue
        value = e.value_json or {}
        if e.rel == "HAS_LIMIT":
            condition = f" ({value['condition']})" if value.get("condition") else ""
            out.append(Fact(e.rel, f"{labels[e.src_id]}, {labels[e.dst_id].lower()}", f"{_money(value.get('cents'))} for {e.valid_from.year}{condition}"))
        elif e.rel == "HAS_RULE":
            out.append(Fact(e.rel, f"{labels[e.src_id]}, {labels[e.dst_id].lower()}", value.get("text", "")))
        else:
            out.append(Fact(e.rel, labels[e.src_id], f"{e.rel.replace('_', ' ').lower()} {labels[e.dst_id]}"))
    return out


# a year with no limit on record is a fact too. saying so is what stops a passage about another year being quoted for it
def _no_figure(session: Session, concepts: list[KGNode], guidance: list[Fact], asked: Optional[int]) -> list[Fact]:
    if asked is None or not concepts or any(f.rel == "HAS_LIMIT" for f in guidance):
        return []
    years = sorted({e.valid_from.year for e in session.scalars(select(KGEdge).where(
        KGEdge.user_id.is_(None), KGEdge.rel == "HAS_LIMIT", KGEdge.src_id.in_([c.id for c in concepts + _parents(session, concepts)]),
        KGEdge.expired_at.is_(None)))})
    if not years:
        return []
    names = ", ".join(c.label or c.key for c in concepts)
    return [Fact("HAS_LIMIT", f"{names}, limit for {asked}",
                 f"not in the guidance, which covers {years[0]} to {years[-1]}. Say this figure isn't available")]


def _parents(session: Session, concepts: list[KGNode]) -> list[KGNode]:
    rows = session.scalars(select(KGEdge.dst_id).where(KGEdge.user_id.is_(None), KGEdge.rel == "IS_A", KGEdge.expired_at.is_(None),
                                                       KGEdge.src_id.in_([c.id for c in concepts]))).all()
    return list(session.scalars(select(KGNode).where(KGNode.id.in_(rows)))) if rows else []


def _shuffled(user: list[Fact], guidance: list[Fact]) -> tuple[list[Fact], list[Fact]]:
    facts = [*user, *guidance]
    values = [f.value for f in facts]
    random.Random(SHUFFLE_SEED).shuffle(values)
    mixed = [replace(f, value=v) for f, v in zip(facts, values)]
    return mixed[:len(user)], mixed[len(user):]


def gather(session: Session, user_id: str, question: str, today: date, *, scrub: Callable = lambda s: s,
           relation: Optional[str] = None, history: bool = False) -> Gathered:
    on_date, start = read_time(question, today)
    window = Window(on_date, start)
    # "what's my food budget?" means this month's limit, so a budget alone narrows to today when no period is named.
    # everything else keeps its history: a summary of only this month would read as "first charged this month"
    history = history or bool(re.search(
        r"\b(history|when|first|start\w*|stop\w*|chang\w*|previous\w*|decid\w*|accept\w*|dismiss\w*|rais\w*|increas\w*"
        r"|decreas\w*|lower\w*|past|ever|before|used to|(?:go|goes|gone|going|went) (?:up|down))\b", question.casefold()))
    budget_window = window if start is not None or history else Window(on_date, on_date)
    words = _words(question) - STOP_WORDS
    all_words = _words(question)
    user: list[Fact] = []
    for merchant in _nodes(session, user_id, "merchant"):
        if _matches(merchant.label or merchant.key, words):
            user += _merchant_facts(session, user_id, merchant, scrub, window)
    user += _stream_facts(session, user_id, words, scrub, window)
    categories = [c for c in _nodes(session, user_id, "category")
                  if set(c.key.casefold().split("_")) - {"and"} & all_words or _matches(c.label, words)]
    for category in categories:
        user += _budget_facts(session, user_id, category, budget_window)
    user += _advice_facts(session, user_id, words, categories, scrub, window)
    if relation:
        user = [f for f in user if f.rel == relation]
    concepts = [] if relation else _linked_concepts(session, question)
    guidance = _guidance_facts(session, user_id, concepts, on_date)
    named_year = re.search(r"\b(?:19|20)\d{2}\b", question)
    guidance += _no_figure(session, concepts, guidance, int(named_year.group()) if named_year else None)
    if config.KG_CHAT == "hybrid-shuffled":
        user, guidance = _shuffled(user, guidance)
    return Gathered(on_date, tuple(user[:MAX_FACTS]), tuple(guidance[:max(MAX_FACTS - len(user), 0)]))
