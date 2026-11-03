"""Build kb/graph.json from the raw extraction and its review, and score the raw extraction (architecture 6b.5).

Scores the model's own output, before review: precision on 30 sampled facts, and recall on the checklist in
eval/kg/recall_checklist.json, written before any extraction ran. Code-made MENTIONED_IN links aren't scored. A sampled
fact counts as wrong when the review rejected or corrected it. Run from the repo root:
    venv/bin/python eval/kg_quality.py
"""
import json
import random
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from app.kg.extract import apply_review, edge_id

SAMPLE_SIZE = 30
SEED = 38


def extracted(graph: dict) -> list[dict]:
    return [e for e in graph["edges"] if e["rel"] != "MENTIONED_IN"]


def precision(graph: dict, review: dict, size: int = SAMPLE_SIZE, seed: int = SEED) -> dict:
    facts = extracted(graph)
    wrong_ids = {edge_id(e) for e in [*review["reject"], *review["correct"]]}
    sample = random.Random(seed).sample(facts, min(size, len(facts)))
    labels = [{"fact": list(edge_id(e)), "value": e["value"], "correct": edge_id(e) not in wrong_ids} for e in sample]
    right = sum(1 for row in labels if row["correct"])
    return {"sampled": len(labels), "correct": right, "precision": right / len(labels) if labels else None,
            "all_facts": len(facts), "all_wrong": sum(1 for e in facts if edge_id(e) in wrong_ids), "labels": labels}


def _found(fact: dict, edges: list[dict]) -> bool:
    for e in edges:
        if e["rel"] != fact["rel"]:
            continue
        same = (e["src"], e["dst"]) == (fact["src"], fact["dst"])
        if not same and not (fact.get("either_way") and (e["dst"], e["src"]) == (fact["src"], fact["dst"])):
            continue
        if "year" in fact and (e["valid_from"][:4] != str(fact["year"]) or (e["value"] or {}).get("cents") != fact["cents"]):
            continue
        if "text_contains" in fact and fact["text_contains"] not in (e["value"] or {}).get("text", ""):
            continue
        return True
    return False


def recall(graph: dict, checklist: list[dict]) -> dict:
    rows = [{**fact, "found": _found(fact, graph["edges"])} for fact in checklist]
    hits = sum(1 for r in rows if r["found"])
    return {"checklist": len(rows), "found": hits, "recall": hits / len(rows) if rows else None, "facts": rows}


def main() -> int:
    kg = REPO / "eval" / "kg"
    raw = json.loads((kg / "raw_graph.json").read_text(encoding="utf-8"))
    review = json.loads((REPO / "kb" / "graph_review.json").read_text(encoding="utf-8"))
    checklist = json.loads((kg / "recall_checklist.json").read_text(encoding="utf-8"))
    reviewed = apply_review(raw, review)
    (REPO / "kb" / "graph.json").write_text(json.dumps(reviewed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result = {"reviewer": review["reviewer"], "precision": precision(raw, review), "recall": recall(raw, checklist),
              "recall_after_review": recall(reviewed, checklist)["found"],
              "first_run_recall": recall(json.loads((kg / "first_run_graph.json").read_text(encoding="utf-8")), checklist)["found"]}
    out = REPO / "eval" / "results" / date.today().isoformat() / "kg_extraction"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    p, r = result["precision"], result["recall"]
    print(f"precision {p['correct']}/{p['sampled']} sampled ({p['all_wrong']} of {p['all_facts']} facts wrong overall)")
    print(f"recall {r['found']}/{r['checklist']}, {result['recall_after_review']} after review, "
          f"{result['first_run_recall']} on the first run")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
