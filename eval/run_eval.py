"""Score the chat, budget and goal agents on the golden scenarios in eval/scenarios/.

Each scenario seeds a synthetic user into a throwaway database and runs the real agent --runs
times. A chat answer is scored on golden facts, semantic similarity and a coherence judge; a
budget or goal run is scored by rule on what the graph decided. Results go to
eval/results/<date>/<model>/, one file per run as it finishes.

Run from the repo root:
    venv/bin/python eval/run_eval.py [--model qwen2.5:14b] [--runs 3] [--only <id>] [--kind budget] [--judge gemma3:12b]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
# make `eval` and `app` importable however the script is launched (same trick as backend/scripts/)
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from cryptography.fernet import Fernet

# app.security fails at import without a key; the eval never decrypts anything
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

import httpx

from app import config
from app.agents.chat_graph import GRAPH_MODES, GRAPH_RETRIEVAL_BUILT
from app.agents.llm import get_chat_model, get_embeddings
from app.rag.store import KnowledgeBase
from eval.agent_runner import run_budget_once, run_goal_once
from eval.fixtures import KINDS, ScenarioError, load_scenarios
from eval.metrics import score_budget, score_goal, score_run, summarize, summarize_budget, summarize_goal
from eval.runner import run_once

RETRIEVAL_MODES = ("none", "vector", "graph", "hybrid", "hybrid-shuffled")
JUDGE_MODEL = "gemma3:12b"
SCENARIOS_DIR = REPO / "eval" / "scenarios"
RESULTS_DIR = REPO / "eval" / "results"


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model", default=config.OLLAMA_MODEL, help="agent model (default: OLLAMA_MODEL)")
    p.add_argument("--judge", default=JUDGE_MODEL, help=f"judge model (default: {JUDGE_MODEL})")
    p.add_argument("--runs", type=int, default=3, help="runs per scenario (default: 3)")
    p.add_argument("--only", help="run just this scenario id")
    p.add_argument("--kind", choices=KINDS, help="run just this kind of scenario (default: all)")
    p.add_argument("--skip-tag", action="append", default=[], metavar="TAG",
                   help="leave out scenarios with this tag; can be repeated")
    p.add_argument("--limit", type=int, help="the first N scenarios of each kind, for a pilot")
    p.add_argument("--retrieval", choices=RETRIEVAL_MODES, help="chat retrieval (default: KG_CHAT)")
    p.add_argument("--kg", choices=("off", "on"), help="graph facts in the budget agent (default: KG_MODE)")
    p.add_argument("--retries", choices=("on", "off"), help="structured-output resend and goal correction (default: on)")
    p.add_argument("--temperature", type=float, default=0.0, help="agent temperature (default: 0)")
    p.add_argument("--seed", type=int, help="agent seed (default: none)")
    p.add_argument("--today", type=date.fromisoformat, help="pin chat scenarios to this date (YYYY-MM-DD)")
    p.add_argument("--label", help="add this condition to the results folder's name")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs must be at least 1")
    return args


# None = ollama unreachable; a bare name also matches "<name>:latest"
def missing_models(models: list[str]):
    try:
        resp = httpx.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        names = {m["name"] for m in resp.json()["models"]}
    except Exception:
        return None
    return [m for m in models if m not in names and f"{m}:latest" not in names]


# kb is None when no chat scenario will run: only chat uses it, the embeddings and the judge
def preflight(agent: str, judge: str, kb) -> list[str]:
    needed = list(dict.fromkeys([agent] if kb is None else [agent, config.OLLAMA_EMBED_MODEL, judge]))
    missing = missing_models(needed)
    if missing is None:
        return [f"Ollama isn't reachable at {config.OLLAMA_BASE_URL}. Start it with: ollama serve"]
    problems = [f"Model {m} isn't pulled. Run: ollama pull {m}" for m in missing]
    if kb is not None and kb.count() == 0:
        problems.append("The knowledge base is empty. Run: venv/bin/python backend/scripts/ingest_kb.py")
    return problems


# a second run on the same day gets -2, -3... so earlier results are never overwritten
def results_dir(root: Path, day: date, model: str, label: Optional[str] = None) -> Path:
    name = model.replace(":", "_").replace("/", "_") + (f"-{label}" if label else "")
    base = root / day.isoformat() / name
    path, n = base, 1
    while path.exists():
        n += 1
        path = base.with_name(f"{base.name}-{n}")
    path.mkdir(parents=True)
    return path


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def _ollama_version() -> str:
    try:
        return httpx.get(f"{config.OLLAMA_BASE_URL}/api/version", timeout=5).json()["version"]
    except Exception:
        return "unknown"


# so two results folders can be refused as not comparable if their scenarios differ
def _scenario_hash(scenarios) -> str:
    digest = hashlib.sha256()
    for s in sorted(scenarios, key=lambda s: s.id):
        digest.update(s.path.read_bytes())
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _pct(x) -> str:
    return f"{x * 100:5.0f}%"


def _print_chat(summary: dict) -> None:
    print(f"\n{'scenario':<28}{'facts':>7}{'sim':>7}{'judge':>7}{'tools':>7}{'errors':>8}")
    rows = list(summary["scenarios"].items()) + [("OVERALL", summary["overall"])]
    for sid, s in rows:
        print(f"{sid:<28}{_pct(s['fact_pass_rate']):>7}{s['similarity_mean']:7.2f}"
              f"{_pct(s['judge_pass_rate']):>7}{_pct(s['tool_accuracy']):>7}{s['errors']:>8}")


def _print_agent(name: str, summary: dict) -> None:
    print(f"\n{name + ' scenario':<28}{'pass':>7}{'all runs':>10}{'errors':>8}")
    for sid, s in [*summary["scenarios"].items(), ("OVERALL", summary)]:
        print(f"{sid:<28}{_pct(s['pass_rate']):>7}{_pct(s['pass_all_runs_rate']):>10}{s['errors']:>8}")
    if summary.get("false_alarm_rate") is not None:
        print(f"false alarms on controls: {_pct(summary['false_alarm_rate']).strip()}")


def _print_table(summary: dict) -> None:
    if summary["overall"]["runs"]:
        _print_chat(summary)
    for name in ("budget", "goal"):
        if name in summary:
            _print_agent(name, summary[name])


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.retrieval in GRAPH_MODES and not GRAPH_RETRIEVAL_BUILT:
        print(f"Can't run --retrieval {args.retrieval} yet: graph retrieval arrives with TPC-39")
        return 2
    if args.retrieval:
        config.KG_CHAT = args.retrieval
    if args.kg:
        config.KG_MODE = args.kg
    if args.retries:
        config.RETRIES_ENABLED = args.retries == "on"
    try:
        scenarios = load_scenarios(SCENARIOS_DIR, only=args.only)
    except ScenarioError as exc:
        print(f"Bad scenario: {exc}")
        return 2
    if args.kind:
        scenarios = [s for s in scenarios if s.kind == args.kind]
    scenarios = [s for s in scenarios if not set(args.skip_tag) & set(s.tags)]
    if args.limit:
        scenarios = [s for kind in KINDS for s in [x for x in scenarios if x.kind == kind][:args.limit]]
    if not scenarios:
        print(f"Bad scenario: nothing to run for --kind {args.kind} --skip-tag {args.skip_tag}")
        return 2
    has_chat = any(s.kind == "chat" for s in scenarios)
    kb = KnowledgeBase() if has_chat else None
    problems = preflight(args.model, args.judge, kb)
    if problems:
        print("Can't run the eval yet:\n" + "\n".join(f"- {p}" for p in problems))
        return 2

    if args.today:
        # a scenario that pins its own date keeps it
        scenarios = [replace(s, today=s.today or args.today) if s.kind == "chat" else s for s in scenarios]
    out = results_dir(RESULTS_DIR, date.today(), args.model, args.label)
    cfg = {"model": args.model, "judge": args.judge, "embeddings": config.OLLAMA_EMBED_MODEL,
           "runs": args.runs, "commit": _git_commit(), "scenarios": [s.id for s in scenarios],
           "scenario_hash": _scenario_hash(scenarios), "ollama_version": _ollama_version(), "label": args.label,
           "retrieval": config.KG_CHAT, "kg_mode": config.KG_MODE, "retries": config.RETRIES_ENABLED,
           "temperature": args.temperature, "seed": args.seed, "today": args.today and args.today.isoformat(),
           "started_at": _now()}
    _write(out / "config.json", cfg)

    model = get_chat_model(temperature=args.temperature, model=args.model, seed=args.seed)
    judge_model = get_chat_model(temperature=0, model=args.judge) if has_chat else None
    embeddings = get_embeddings() if has_chat else None
    audit = out / "audit.jsonl"
    scored = {kind: [] for kind in KINDS}
    for scenario in scenarios:
        for run in range(1, args.runs + 1):
            print(f"{scenario.id} run {run}/{args.runs}...", flush=True)
            if scenario.kind == "chat":
                rec = run_once(scenario, model=model, kb=kb, audit_path=audit, run=run)
                item = {"record": rec.to_dict(),
                        "scores": score_run(rec, scenario, embeddings=embeddings, judge_model=judge_model)}
            elif scenario.kind == "budget":
                record = run_budget_once(scenario, model=model, audit_path=audit, run=run).to_dict()
                item = {"record": record, "scores": score_budget(record, scenario)}
            else:
                record = run_goal_once(scenario, model=model, audit_path=audit, run=run).to_dict()
                item = {"record": record, "scores": score_goal(record, scenario)}
            # written now, so a crash or ctrl-c keeps every finished run
            _write(out / f"{scenario.id}.run{run}.json", item)
            scored[scenario.kind].append(item)

    by_kind = lambda kind: {s.id: s for s in scenarios if s.kind == kind}
    summary = summarize(scored["chat"], tags={s.id: s.tags for s in by_kind("chat").values()})
    if scored["budget"]:
        summary["budget"] = summarize_budget(scored["budget"], by_kind("budget"))
    if scored["goal"]:
        summary["goal"] = summarize_goal(scored["goal"], by_kind("goal"))
    _write(out / "summary.json", summary)
    _write(out / "config.json", {**cfg, "finished_at": _now()})
    _print_table(summary)
    print(f"\nResults: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
