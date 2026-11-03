import json
from datetime import date

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from app.db.models import KGEdge, KGNode
from app.kg import query
from app.kg.extract import (Concept, Triple, check_triples, concept_names, limit_problem, load_concepts, mentions,
                            merge_edges)
from app.kg.guidance import load_guidance
from eval.fixtures import temp_db

CONCEPTS = [Concept("ira", "Individual retirement account", ("IRA",)), Concept("contribution-limit", "Annual contribution limit"),
            Concept("roth-ira", "Roth IRA")]
NAMES = concept_names(CONCEPTS)
IRA_TEXT = "For 2026, the total can't be more than:\n- $7,500 ($8,600 if you're age 50 or older)\nFor 2025, $7,000."


def limit(year=2026, dollars=7500, **kw):
    return Triple(subject="IRA", relation="HAS_LIMIT", object="Annual contribution limit", tax_year=year, dollars=dollars, **kw)


def check(*triples, text=IRA_TEXT):
    return check_triples(list(triples), text, NAMES, source="irs", chunk_index=3)


def test_a_limit_keeps_its_tax_year_as_a_validity_interval():
    [edge] = check(limit()).edges
    assert (edge["valid_from"], edge["valid_to"]) == ("2026-01-01", "2027-01-01")
    assert edge["value"]["cents"] == 750000 and edge["evidence"] == {"source": "irs", "chunk_index": 3}


@pytest.mark.parametrize("triple, why", [
    (limit(year=None), "no tax year"),
    (limit(year=2030), "tax year isn't in the chunk"),
    (limit(dollars=9999), "amount isn't in the chunk"),
    (limit(dollars=None), "no amount"),
])
def test_a_limit_that_cant_be_checked_is_dropped(triple, why):
    result = check(triple)
    assert result.edges == [] and result.dropped[0]["why"] == why


def test_a_limit_whose_year_is_far_from_its_amount_is_dropped():
    text = "For 2026, see below. " + "x" * 300 + " The amount is $7,500."
    assert limit_problem(limit(), text) == "year and amount are too far apart"


def test_an_amount_doesnt_match_inside_a_bigger_number():
    assert limit_problem(limit(dollars=7500), "In 2026 it was $27,500.") == "amount isn't in the chunk"


def test_an_unknown_concept_is_logged_and_never_added():
    result = check(Triple(subject="Crypto", relation="IS_A", object="IRA"))
    assert result.edges == [] and result.unknown == ["Crypto"]


def test_a_rule_needs_text():
    assert check(Triple(subject="Roth IRA", relation="HAS_RULE", object="IRA")).dropped[0]["why"] == "a rule with no text"


def test_a_concept_cant_relate_to_itself():
    assert check(Triple(subject="IRA", relation="IS_A", object="ira")).edges == []


def test_mentions_are_whole_words():
    assert mentions("A Roth IRA is an IRA.", CONCEPTS) == ["ira", "roth-ira"]
    assert mentions("Miracle growth.", CONCEPTS) == []


def test_merge_keeps_the_first_and_reports_a_disagreement():
    base = {"src": "a", "rel": "IS_A", "dst": "b", "valid_from": "2000-01-01", "value": None}
    same, other = dict(base), {**base, "value": {"x": 1}}
    kept, conflicts = merge_edges([base, same, other])
    assert kept == [base] and len(conflicts) == 1


def graph_file(tmp_path, edges):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({"concepts": [{"key": "ira", "label": "IRA"}, {"key": "contribution-limit", "label": "Limit"}],
                                "sources": [{"slug": "irs", "title": "IRS", "url": "https://irs.gov"}], "edges": edges}))
    return path


def year_edge(year, cents):
    return {"src": "ira", "rel": "HAS_LIMIT", "dst": "contribution-limit", "value": {"cents": cents, "condition": None},
            "evidence": {"source": "irs", "chunk_index": 1}, "valid_from": f"{year}-01-01", "valid_to": f"{year + 1}-01-01"}


def test_each_year_is_its_own_edge_and_as_of_returns_the_right_one(tmp_path):
    path = graph_file(tmp_path, [year_edge(2026, 750000), year_edge(2025, 700000)])
    with temp_db() as Session, Session() as s:
        assert load_guidance(s, path).skipped == []
        cents = lambda day: [e.value_json["cents"] for e in query.as_of(s, user_id=None, on_date=day).edges]
        assert cents(date(2025, 6, 1)) == [700000]
        assert cents(date(2026, 6, 1)) == [750000]
        assert cents(date(2024, 6, 1)) == []


def test_reloading_adds_nothing_and_a_changed_fact_is_corrected(tmp_path):
    path = graph_file(tmp_path, [year_edge(2026, 750000)])
    with temp_db() as Session, Session() as s:
        load_guidance(s, path)
        load_guidance(s, path)
        assert s.query(KGEdge).count() == 1
        load_guidance(s, graph_file(tmp_path, [year_edge(2026, 760000)]))
        assert s.query(KGEdge).count() == 2
        [current] = s.scalars(select(KGEdge).where(KGEdge.expired_at.is_(None))).all()
        assert current.value_json["cents"] == 760000


def test_mentioned_in_points_at_a_source(tmp_path):
    edge = {"src": "ira", "rel": "MENTIONED_IN", "dst": "irs", "value": {"chunks": [1, 2]}, "evidence": {"source": "irs"},
            "valid_from": "2000-01-01", "valid_to": None}
    with temp_db() as Session, Session() as s:
        assert load_guidance(s, graph_file(tmp_path, [edge])).skipped == []
        source = s.scalars(select(KGNode).where(KGNode.kind == "source")).one()
        assert source.user_id is None and source.props_json["url"] == "https://irs.gov"


def test_committed_concepts_load_and_cover_the_bridges():
    from pathlib import Path
    from app.kg.project import CATEGORY_CONCEPTS, SUBTYPE_CONCEPTS
    concepts = load_concepts(Path(__file__).resolve().parents[2] / "kb" / "concepts.json")
    assert len(concepts) >= 40 and len({c.key for c in concepts}) == len(concepts)
    keys = {c.key for c in concepts}
    assert set(CATEGORY_CONCEPTS.values()) <= keys and set(SUBTYPE_CONCEPTS.values()) <= keys


def test_aliases_arent_shared_between_concepts():
    from pathlib import Path
    concepts = load_concepts(Path(__file__).resolve().parents[2] / "kb" / "concepts.json")
    names = [n for c in concepts for n in {x.casefold() for x in (c.key, c.label, *c.aliases)}]
    assert sorted(n for n in set(names) if names.count(n) > 1) == []


@pytest.mark.parametrize("subject, obj", [("Annual contribution limit", "IRA"), ("IRA", "Roth IRA")])
def test_a_limit_must_go_from_an_account_to_a_kind_of_limit(subject, obj):
    triple = Triple(subject=subject, relation="HAS_LIMIT", object=obj, tax_year=2026, dollars=7500)
    result = check(triple)
    assert result.edges == [] and result.dropped[0]["why"].startswith("a limit must be")


def test_review_rejects_corrects_and_adds():
    from app.kg.extract import apply_review
    edge = lambda src, cents: {"src": src, "rel": "HAS_LIMIT", "dst": "limit", "value": {"cents": cents}, "valid_from": "2026-01-01", "valid_to": "2027-01-01"}
    raw = {"concepts": [], "sources": [], "edges": [edge("a", 1), edge("b", 2)]}
    wrong = {"src": "a", "rel": "HAS_LIMIT", "dst": "limit", "valid_from": "2026-01-01"}
    review = {"reject": [wrong], "correct": [{"src": "b", "rel": "HAS_LIMIT", "dst": "limit", "valid_from": "2026-01-01", "set": {"src": "c"}, "why": "w"}],
              "add": [{**edge("d", 4), "why": "w"}]}
    out = apply_review(raw, review)
    assert sorted(e["src"] for e in out["edges"]) == ["c", "d"] and all("why" not in e for e in out["edges"])
    assert len(raw["edges"]) == 2      # the raw output is left alone


def test_a_review_entry_matching_nothing_fails_loudly():
    from app.kg.extract import apply_review
    with pytest.raises(ValueError, match="isn't there"):
        apply_review({"edges": []}, {"reject": [{"src": "a", "rel": "IS_A", "dst": "b", "valid_from": "2000-01-01"}], "correct": [], "add": []})


# the ticket's done-when, on the committed graph
def test_committed_graph_has_year_specific_limits(tmp_path):
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "kb" / "graph.json"
    with temp_db() as Session, Session() as s:
        assert load_guidance(s, path).skipped == []
        ira = s.scalars(select(KGNode).where(KGNode.kind == "concept", KGNode.key == "ira")).one()

        def limit(day):
            edges = query.as_of(s, user_id=None, on_date=day).edges
            nodes = {n.id: n.key for n in s.scalars(select(KGNode))}
            return [e.value_json["cents"] for e in edges if e.src_id == ira.id and e.rel == "HAS_LIMIT"
                    and nodes[e.dst_id] == "contribution-limit"]
        assert limit(date(2026, 6, 1)) == [750000]
        assert limit(date(2025, 6, 1)) == [700000]
        assert limit(date(2024, 6, 1)) == [700000]
        assert limit(date(2023, 6, 1)) == [650000]
