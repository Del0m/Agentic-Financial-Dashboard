"""Extract guidance facts from every knowledge base chunk, for a person to read before committing kb/graph.json.

Runs the model once per chunk, so it takes a while. Writes eval/kg/raw_graph.json and eval/kg/raw_report.json, which
lists everything dropped, every name outside kb/concepts.json, and any facts that disagreed. Run from the repo root:
    venv/bin/python backend/scripts/extract_kg.py [--model qwen2.5:14b]
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))

from langchain_core.messages import HumanMessage, SystemMessage

from app import config
from app.agents.budget_llm import invoke_structured
from app.agents.llm import get_chat_model
from app.kg.extract import (ALWAYS, Extraction, check_triples, concept_names, extract_messages, load_concepts,
                            mentions, merge_edges)
from app.rag.chunk import chunk_source


EXTRACT_NUM_PREDICT = 4096


def run(model, kb_dir: Path, sources: list[Path]) -> tuple[dict, dict]:
    concepts = load_concepts(kb_dir / "concepts.json")
    names = concept_names(concepts)
    edges, dropped, unknown, failed, seen_sources, mentioned = [], [], {}, [], {}, {}
    for path in sources:
        for chunk in chunk_source(path):
            slug = path.stem
            seen_sources[slug] = {"slug": slug, "title": chunk.title, "url": chunk.source_url}
            for key in mentions(chunk.text, concepts):
                mentioned.setdefault((key, slug), []).append(chunk.index)
            system, human = extract_messages(concepts, chunk.text)
            print(f"{chunk.id}...", flush=True)
            result = invoke_structured(model, Extraction, [SystemMessage(system), HumanMessage(human)])
            if result is None:
                failed.append(chunk.id)
                continue
            checked = check_triples(result.triples, chunk.text, names, source=slug, chunk_index=chunk.index)
            edges += checked.edges
            dropped += checked.dropped
            for name in checked.unknown:
                unknown[name] = unknown.get(name, 0) + 1
    merged, conflicts = merge_edges(edges)
    merged += [{"src": key, "rel": "MENTIONED_IN", "dst": slug, "value": {"chunks": chunks}, "evidence": {"source": slug},
                "valid_from": ALWAYS.isoformat(), "valid_to": None} for (key, slug), chunks in sorted(mentioned.items())]
    graph = {"concepts": [{"key": c.key, "label": c.label} for c in concepts],
             "sources": [seen_sources[s] for s in sorted(seen_sources)], "edges": merged}
    report = {"dropped": dropped, "unknown_names": dict(sorted(unknown.items())), "conflicts": conflicts,
              "failed_chunks": failed, "kept": len(merged)}
    return graph, report


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=config.OLLAMA_MODEL)
    args = p.parse_args(argv)
    sources = sorted((config.KB_DIR / "sources").glob("*.md")) + sorted((config.KB_DIR / "sources" / "local").glob("*.md"))
    # a passage can hold many facts, and the chat agent's reply cap cut two of them off on the first run
    config.LLM_NUM_PREDICT = EXTRACT_NUM_PREDICT
    graph, report = run(get_chat_model(temperature=0, model=args.model), config.KB_DIR, sources)
    # never kb/graph.json directly: that's raw_graph.json plus the recorded review, built by eval/kg_quality.py
    (REPO / "eval" / "kg" / "raw_graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (REPO / "eval" / "kg" / "raw_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['kept']} edges kept, {len(report['dropped'])} dropped, {len(report['unknown_names'])} unknown names, "
          f"{len(report['conflicts'])} conflicts, {len(report['failed_chunks'])} failed chunks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
