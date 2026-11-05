"""Run every benchmark condition in order, on one commit (docs/superpowers/specs/2026-11-03-tpc-27-full-benchmark-design.md).

A finished condition is skipped on a restart. Each runs in its own process, so one condition's settings can't leak into
the next. Run from the repo root:
    venv/bin/python eval/run_benchmark.py --pilot
    venv/bin/python eval/run_benchmark.py --collect
    venv/bin/python eval/run_benchmark.py
"""
import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

MODELS = ("qwen2.5:14b", "llama3.1:8b", "mistral")
ABLATION_MODEL = MODELS[0]
RESULTS_DIR = REPO / "eval" / "results"
TODAY = "2026-11-06"
VARIATION_TEMPERATURE = "0.7"
VARIATION_SEEDS = (1, 2, 3)
PILOT_LIMIT = 4         # per kind, so about 10 scenarios a condition


@dataclass(frozen=True)
class Condition:
    label: str
    args: tuple[str, ...]


def _safe(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def eval_command(model: str, condition: Condition, baseline: tuple[str, ...], limit=None) -> list[str]:
    command = [sys.executable, "eval/run_eval.py", "--model", model, "--label", condition.label, "--today", TODAY,
               *baseline, *condition.args]
    return command + (["--limit", str(limit)] if limit else [])


# the baseline changes one thing per row. before the graph exists, it's vector with the graph off, and the conditions
# that need the graph aren't run
def conditions(graph_built: bool) -> list[tuple[str, Condition]]:
    baseline_label = "baseline"
    rows = [(model, Condition(baseline_label, ())) for model in MODELS]
    chat = ("none", "vector", "graph", "hybrid-shuffled") if graph_built else ("none",)
    rows += [(ABLATION_MODEL, Condition(f"chat-{mode}", ("--retrieval", mode, "--kind", "chat"))) for mode in chat]
    if graph_built:
        rows.append((ABLATION_MODEL, Condition("budget-kg-off", ("--kg", "off", "--kind", "budget"))))
    rows += [(ABLATION_MODEL, Condition("budget-retries-off", ("--retries", "off", "--kind", "budget"))),
             (ABLATION_MODEL, Condition("goal-retries-off", ("--retries", "off", "--kind", "goal")))]
    rows += [(ABLATION_MODEL, Condition(f"variation-seed{seed}", ("--temperature", VARIATION_TEMPERATURE, "--seed", str(seed),
                                                                 "--runs", "1"))) for seed in VARIATION_SEEDS]
    return rows


def _commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True).stdout.strip()


def dirty_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def expected_config(command: list[str]) -> dict:
    # Use the evaluator's parser and scenario selection without starting any models.
    from app import config
    from eval.run_eval import parse_args, load_scenarios, SCENARIOS_DIR, _scenario_hash, spread, KINDS
    args = parse_args(command[2:])
    scenarios = [s for s in load_scenarios(SCENARIOS_DIR, only=args.only)
                 if (not args.kind or s.kind == args.kind) and not set(args.skip_tag) & set(s.tags)]
    if args.limit:
        scenarios = [s for kind in KINDS for s in spread([s for s in scenarios if s.kind == kind], args.limit)]
    return {"model": args.model, "label": args.label, "runs": args.runs,
            "scenarios": [s.id for s in scenarios], "scenario_hash": _scenario_hash(scenarios),
            "retrieval": args.retrieval or config.KG_CHAT, "kg_mode": args.kg or config.KG_MODE,
            "retries": args.retries == "on" if args.retries else config.RETRIES_ENABLED,
            "temperature": args.temperature, "seed": args.seed, "today": args.today and args.today.isoformat(),
            "judge": args.judge, "embeddings": config.OLLAMA_EMBED_MODEL}


def is_finished(model: str, label: str, commit: str, *, expected: dict, root: Path = None) -> bool:
    root = RESULTS_DIR if root is None else root
    name = re.compile(re.escape(f"{_safe(model)}-{label}") + r"(?:-[2-9]\d*|-1\d+)?")
    for path in root.glob("*/*/summary.json"):
        if not name.fullmatch(path.parent.name):
            continue
        try:
            config = json.loads((path.parent / "config.json").read_text())
            summary = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(config, dict) or not isinstance(summary, dict):
            continue
        if (config.get("commit") == commit and config.get("finished_at")
                and all(key in config and config[key] == value for key, value in expected.items())
                and all((path.parent / f"{sid}.run{n}.json").is_file()
                        for sid in expected["scenarios"] for n in range(1, expected["runs"] + 1))):
            return True
    return False


def run(command: list[str]) -> float:
    started = time.perf_counter()
    print("$ " + " ".join(command[1:]), flush=True)
    subprocess.run(command, cwd=REPO, check=True)
    return time.perf_counter() - started


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--pilot", action="store_true", help="about 10 scenarios a condition, and time each model")
    p.add_argument("--collect", action="store_true", help="only collect budget proposals for Claude to decide on")
    p.add_argument("--allow-dirty", action="store_true", help="pilot only: run with uncommitted changes")
    args = p.parse_args(argv)
    if args.allow_dirty and not args.pilot:
        p.error("--allow-dirty is only for --pilot")
    dirty = dirty_files()
    if dirty and not args.allow_dirty:
        print("Can't run with uncommitted changes, because the results would record the wrong code:\n" + "\n".join(dirty))
        return 2
    from app.agents.chat_graph import GRAPH_RETRIEVAL_BUILT
    baseline = ("--retrieval", "hybrid", "--kg", "on") if GRAPH_RETRIEVAL_BUILT else ("--skip-tag", "needs_graph")
    improvement = [sys.executable, "eval/budget_improvement.py"]
    if args.collect:
        for model in MODELS:
            run([*improvement, "--collect", "--model", model])
        run([*improvement, "--merge"])
        return 0
    commit, timings = _commit(), {}
    for model, condition in conditions(GRAPH_RETRIEVAL_BUILT):
        if args.pilot:
            condition = replace(condition, label=f"pilot-{condition.label}")
        if args.pilot and "--runs" not in condition.args:
            command = eval_command(model, condition, baseline, PILOT_LIMIT) + ["--runs", "1"]
        else:
            command = eval_command(model, condition, baseline, PILOT_LIMIT if args.pilot else None)
        if not args.pilot and is_finished(model, condition.label, commit, expected=expected_config(command)):
            print(f"Skipping {model} {condition.label}: already finished on this commit", flush=True)
            continue
        timings[f"{model} {condition.label}"] = round(run(command), 1)
    if args.pilot:
        timings["no-AI baseline"] = round(run([*improvement, "--baseline"]), 1)
        for model in MODELS:
            timings[f"{model} one improvement user"] = round(run([*improvement, "--collect", "--model", model,
                                                                  "--only", "improve-new-club"]), 1)
    else:
        run([*improvement, "--baseline"])
        for model in MODELS:
            run([*improvement, "--replay", "--model", model])
    print(json.dumps(timings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
