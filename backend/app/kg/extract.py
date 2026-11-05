"""Guidance facts from one knowledge base chunk (tpc-38, architecture 6b.2).

The model proposes triples against a fixed ontology. Code decides what's kept, because a limit in the wrong year is
worse than no limit: concepts must be in kb/concepts.json, and a limit's year and amount must both be in the chunk,
close together.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

LIMIT_WINDOW = 200      # characters between a limit's year and its amount, so one row can't borrow another's year
# a limit is on an account or plan, and its object says which kind. the first run stored IRA limits as
# contribution-limit -> income-limit, with nothing to say whose they were
LIMIT_KINDS = frozenset({"contribution-limit", "catch-up-contribution", "income-limit", "deposit-insurance-limit"})
ALWAYS = date(2000, 1, 1)

EXTRACT_PROMPT = (
    "You extract facts from one passage of public financial guidance. Use only what the passage states.\n"
    "Each fact has a subject and object. Both must be copied exactly from this list, and never be a sentence:\n{concepts}\n"
    "Relations:\n"
    "- IS_A: the passage says the subject is a kind of the object. Don't infer it from them being mentioned together.\n"
    "- HAS_LIMIT: an account or plan has a dollar limit for one tax year. The subject is the account or plan, and the "
    "object is the kind of limit. Give tax_year and dollars, and condition when it applies to some people only. Skip it "
    "if the passage names no year for that amount.\n"
    "- HAS_RULE: the subject has a rule about the object. Put the rule in text, in one short sentence from the passage.\n"
    "- ALTERNATIVE_TO: the passage offers the subject as an alternative to the object.\n"
    "For \"For 2026, your IRA contributions can't be more than $7,500\", the fact is subject \"Individual retirement "
    "account\", relation HAS_LIMIT, object \"Annual contribution limit\", tax_year 2026, dollars 7500.\n"
    "Give every limit stated for every year as its own fact. Most passages have few facts. If none fits, return an empty "
    "list. Answer in JSON."
)


class Triple(BaseModel):
    subject: str
    relation: Literal["IS_A", "HAS_LIMIT", "HAS_RULE", "ALTERNATIVE_TO"]
    object: str
    tax_year: Optional[int] = None
    dollars: Optional[float] = None
    condition: Optional[str] = None
    text: Optional[str] = None


class Extraction(BaseModel):
    triples: list[Triple]


@dataclass(frozen=True)
class Concept:
    key: str
    label: str
    aliases: tuple[str, ...] = ()


@dataclass
class Checked:
    edges: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)    # names outside the list, for review. never added by themselves


def load_concepts(path: Path) -> list[Concept]:
    return [Concept(c["key"], c["label"], tuple(c.get("aliases", ()))) for c in json.loads(path.read_text(encoding="utf-8"))]


def concept_names(concepts: list[Concept]) -> dict[str, str]:
    names = {}
    for c in concepts:
        for name in (c.key, c.label, *c.aliases):
            names[name.strip().casefold()] = c.key
    return names


def extract_messages(concepts: list[Concept], text: str) -> tuple[str, str]:
    listed = "\n".join(f"- {c.label}" for c in concepts)
    return EXTRACT_PROMPT.format(concepts=listed), f"Passage:\n{text}"


def _money_positions(text: str, dollars: float) -> list[int]:
    whole = f"{dollars:,.0f}" if float(dollars).is_integer() else f"{dollars:,.2f}"
    plain = whole.replace(",", "")
    return [m.start() for form in {whole, plain} for m in re.finditer(rf"(?<![\d,]){re.escape(form)}(?![\d,])", text)]


def _year_positions(text: str, year: int) -> list[int]:
    return [m.start() for m in re.finditer(rf"\b{year}\b", text)]


# the year and amount have to be stated together in the chunk. a limit that fails is dropped, never guessed
def limit_problem(t: Triple, text: str) -> Optional[str]:
    if t.tax_year is None:
        return "no tax year"
    if t.dollars is None or t.dollars <= 0:
        return "no amount"
    years, amounts = _year_positions(text, t.tax_year), _money_positions(text, t.dollars)
    if not years:
        return "tax year isn't in the chunk"
    if not amounts:
        return "amount isn't in the chunk"
    if min(abs(y - a) for y in years for a in amounts) > LIMIT_WINDOW:
        return "year and amount are too far apart"
    return None


def check_triples(triples: list[Triple], text: str, names: dict[str, str], *, source: str, chunk_index: int) -> Checked:
    out = Checked()
    for t in triples:
        src, dst = names.get(t.subject.strip().casefold()), names.get(t.object.strip().casefold())
        for raw, key in ((t.subject, src), (t.object, dst)):
            if key is None:
                out.unknown.append(raw.strip())
        row = t.model_dump()
        if src is None or dst is None:
            out.dropped.append({**row, "why": "concept isn't in the list", "source": source, "chunk_index": chunk_index})
            continue
        if src == dst:
            out.dropped.append({**row, "why": "a concept related to itself", "source": source, "chunk_index": chunk_index})
            continue
        evidence = {"source": source, "chunk_index": chunk_index}
        if t.relation == "HAS_LIMIT":
            problem = limit_problem(t, text)
            if problem is None and (src in LIMIT_KINDS or dst not in LIMIT_KINDS):
                problem = "a limit must be from an account or plan to a kind of limit"
            if problem:
                out.dropped.append({**row, "why": problem, "source": source, "chunk_index": chunk_index})
                continue
            value = {"cents": round(t.dollars * 100), "condition": t.condition}
            out.edges.append({"src": src, "rel": "HAS_LIMIT", "dst": dst, "value": value, "evidence": evidence,
                              "valid_from": f"{t.tax_year}-01-01", "valid_to": f"{t.tax_year + 1}-01-01"})
        elif t.relation == "HAS_RULE":
            if not (t.text or "").strip():
                out.dropped.append({**row, "why": "a rule with no text", "source": source, "chunk_index": chunk_index})
                continue
            out.edges.append({"src": src, "rel": "HAS_RULE", "dst": dst, "value": {"text": t.text.strip()},
                              "evidence": evidence, "valid_from": ALWAYS.isoformat(), "valid_to": None})
        else:
            out.edges.append({"src": src, "rel": t.relation, "dst": dst, "value": None, "evidence": evidence,
                              "valid_from": ALWAYS.isoformat(), "valid_to": None})
    return out


def edge_id(e: dict) -> tuple:
    return (e["src"], e["rel"], e["dst"], e["valid_from"])


# raw output plus a recorded review gives kb/graph.json, so what the model did and what review changed stay separate.
# a review entry that matches nothing fails loudly, because it would mean the review doesn't describe this output
def apply_review(graph: dict, review: dict) -> dict:
    by_id = {edge_id(e): e for e in graph["edges"]}
    for entry in review["reject"]:
        if edge_id(entry) not in by_id:
            raise ValueError(f"review rejects a fact that isn't there: {edge_id(entry)}")
        del by_id[edge_id(entry)]
    for entry in review["correct"]:
        if edge_id(entry) not in by_id:
            raise ValueError(f"review corrects a fact that isn't there: {edge_id(entry)}")
        fixed = {**by_id.pop(edge_id(entry)), **entry["set"]}
        by_id[edge_id(fixed)] = fixed
    for entry in review["add"]:
        by_id[edge_id(entry)] = {k: v for k, v in entry.items() if k != "why"}
    edges = sorted(by_id.values(), key=lambda e: (e["valid_from"], e["src"], e["rel"], e["dst"]))
    return {**graph, "edges": edges}


# which concepts a chunk names, by whole word: the bridge back to the text it came from. code, not the model
def mentions(text: str, concepts: list[Concept]) -> list[str]:
    found = []
    for c in concepts:
        for name in (c.label, *c.aliases):
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE):
                found.append(c.key)
                break
    return found


# one edge per fact and period: the first wins, in chunk order, and a different value is listed for the hand review
def merge_edges(edges: list[dict]) -> tuple[list[dict], list[dict]]:
    kept, conflicts = {}, []
    for edge in edges:
        key = (edge["src"], edge["rel"], edge["dst"], edge["valid_from"])
        if key not in kept:
            kept[key] = edge
        elif kept[key]["value"] != edge["value"]:
            conflicts.append({"kept": kept[key], "other": edge})
    return sorted(kept.values(), key=lambda e: (e["valid_from"], e["src"], e["rel"], e["dst"])), conflicts
