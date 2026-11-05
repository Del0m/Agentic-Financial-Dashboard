"""Build the paper's result tables from one benchmark date's folders (TPC-28).

Reads only saved results: no model calls. Run from the repo root:
    venv/bin/python eval/make_tables.py eval/results/2026-11-05
It writes <folder>/tables.md. Comparisons come from <folder>/comparisons/*.json, made by eval/compare.py.

A folder named <label>-2 (or -3 ...) is a rerun of a condition cut off part way; the newest finished one is used.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from eval.compare import agent_passed, budget_passed, chat_passed, load_runs, scenario_passes  # noqa: E402

MODELS = ("qwen2.5_14b", "llama3.1_8b", "mistral")
ABLATION = "qwen2.5_14b"
CHAT_MODES = (("none", "chat-none"), ("vector", "chat-vector"), ("graph", "chat-graph"),
              ("hybrid (baseline)", "baseline"), ("hybrid, shuffled facts", "chat-hybrid-shuffled"))
COMPARISONS = (("Vector retrieval", "vector-vs-none"), ("Graph alone", "graph-vs-none"),
               ("Graph added to vector", "hybrid-vs-vector"), ("Real vs shuffled graph facts", "hybrid-vs-shuffled"),
               ("Graph in the budget agent", "budget-kg-on-vs-off"), ("Budget retries", "budget-retries-on-vs-off"),
               ("Goal retries", "goal-retries-on-vs-off"))


def finished(root: Path, name: str) -> Optional[Path]:
    pattern = re.compile(re.escape(name) + r"(?:-(\d+))?")
    found = []
    for path in root.iterdir():
        m = pattern.fullmatch(path.name)
        if not m or not (path / "summary.json").is_file():
            continue
        config = json.loads((path / "config.json").read_text())
        if config.get("finished_at"):
            found.append((int(m.group(1) or 1), path))
    return max(found)[1] if found else None


def pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{x * 100:.0f}%"


def num(x: Optional[float], digits: int = 1) -> str:
    return "–" if x is None else f"{x:.{digits}f}"


def majority(folder: Path, kind: str) -> tuple[int, int]:
    passed = {"chat": chat_passed, "budget": budget_passed, "goal": agent_passed}[kind]
    _, runs = load_runs(folder)
    items = [v for v in runs.values() if v[0]["record"].get("kind", "chat") == kind]
    return sum(1 for v in items if scenario_passes(v, passed)), len(items)


def frac(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]}" if pair[1] else "–"


def table(head: list[str], rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head), *("| " + " | ".join(r) + " |" for r in rows), ""]


def models_section(root: Path) -> list[str]:
    rows_chat, rows_agent = [], []
    for model in MODELS:
        folder = finished(root, f"{model}-baseline")
        if folder is None:
            continue
        s = json.loads((folder / "summary.json").read_text())
        o, b, g = s["overall"], s.get("budget", {}), s.get("goal", {})
        name = model.replace("_", ":")
        rows_chat.append([name, frac(majority(folder, "chat")), pct(o["fact_pass_all_runs_rate"]), pct(o["judge_pass_rate"]),
                          num(o["similarity_mean"], 2), num(o["specific_mean"]), pct(o["tool_accuracy"]),
                          pct(s["by_tag"].get("abstain", {}).get("fact_pass_rate")), pct(o["temporal_rate"]),
                          num(o["latency_mean_s"]), num(o["latency_p95_s"])])
        rows_agent.append([name, frac(majority(folder, "budget")), pct(b.get("checks", {}).get("cause")),
                           pct(b.get("checks", {}).get("action")), pct(b.get("false_alarm_rate")),
                           num(b.get("time_to_flag_days_mean")), frac(majority(folder, "goal")),
                           num(b.get("latency_mean_s")), num(g.get("latency_mean_s"))])
    return (["## 1. Model comparison (baseline settings: hybrid retrieval, graph on, retries on)", "",
             "Each scenario ran 3 times at temperature 0 and passes on at least 2 of 3. Rates are per run; "
             "pass^3 is the share of scenarios passing on all 3 runs.", "", "### Chat", ""]
            + table(["Model", "Fact pass (scenarios)", "pass^3", "Judge pass", "Similarity", "Specificity (1–5)",
                     "Tool accuracy", "Abstention", "Temporal", "Latency mean (s)", "Latency p95 (s)"], rows_chat)
            + ["### Budget and goal agents", ""]
            + table(["Model", "Budget pass (scenarios)", "Cause", "Action", "False alarms", "Days to flag",
                     "Goal pass (scenarios)", "Budget latency (s)", "Goal latency (s)"], rows_agent))


def modes_section(root: Path) -> list[str]:
    rows = []
    for mode, label in CHAT_MODES:
        folder = finished(root, f"{ABLATION}-{label}")
        if folder is None:
            continue
        s = json.loads((folder / "summary.json").read_text())
        o, tags = s["overall"], s["by_tag"]
        rows.append([mode, frac(majority(folder, "chat")), pct(o["fact_pass_all_runs_rate"]),
                     pct(tags.get("temporal", {}).get("fact_pass_rate")), pct(tags.get("graph_suited", {}).get("fact_pass_rate")),
                     pct(tags.get("graph_unsuited", {}).get("fact_pass_rate")), pct(tags.get("abstain", {}).get("fact_pass_rate")),
                     pct(o["judge_pass_rate"]), pct(o["tool_accuracy"]), num(o["latency_mean_s"])])
    return (["## 2. Chat retrieval modes (`qwen2.5:14b`)", ""]
            + table(["Retrieval", "Fact pass (scenarios)", "pass^3", "Temporal runs", "Graph-suited runs",
                     "Graph-unsuited runs", "Abstention runs", "Judge pass", "Tool accuracy", "Latency mean (s)"], rows))


def verdicts_section(root: Path) -> list[str]:
    rows = []
    for question, name in COMPARISONS:
        path = root / "comparisons" / f"{name}.json"
        if not path.is_file():
            continue
        r = json.loads(path.read_text())
        for group, g in r["groups"].items():
            verdict = g["verdict"]
            if r.get("uninformative") and all(r["uninformative"].values()):
                verdict = "uninformative (fired on <5% of calls)"
            rows.append([question if group == "all" else "", group, str(g["scenarios"]), f"{g['passed_with']} vs {g['passed_without']}",
                         str(g["wins"]), str(g["losses"]), verdict])
    return (["## 3. Preregistered comparisons", "",
             "Wins pass only with the technology, losses only without it. Wins minus losses of 2 or more helps, "
             "-2 or less hurts. Per-scenario detail is in `comparisons/*.md`.", ""]
            + table(["Question", "Group", "Scenarios", "Passed with vs without", "Wins", "Losses", "Verdict"], rows))


def variation_section(root: Path) -> list[str]:
    folders = [finished(root, f"{ABLATION}-variation-seed{n}") for n in (1, 2, 3)]
    if any(f is None for f in folders):
        return []
    rows = []
    for kind, passed in (("chat", chat_passed), ("budget", budget_passed), ("goal", agent_passed)):
        per_seed, by_scenario = [], {}
        for folder in folders:
            _, runs = load_runs(folder)
            items = {sid: v for sid, v in runs.items() if v[0]["record"].get("kind", "chat") == kind}
            per_seed.append(f"{sum(1 for v in items.values() if passed(v[0]))}/{len(items)}")
            for sid, v in items.items():
                by_scenario.setdefault(sid, []).append(passed(v[0]))
        majority_count = sum(1 for v in by_scenario.values() if sum(v) * 2 > len(v))
        rows.append([kind, *per_seed, f"{majority_count}/{len(by_scenario)}"])
    return (["## 4. Variation (`qwen2.5:14b`, temperature 0.7, one pass per seed)", ""]
            + table(["Kind", "Seed 1", "Seed 2", "Seed 3", "Pass on 2 of 3 seeds"], rows))


def improvement_section(root: Path) -> list[str]:
    sources = [("No AI (close logic only)", root / "improvement-baseline" / "no-ai" / "results.json")]
    sources += [(m.replace("_", ":"), root / "improvement-replay" / m / "results.json") for m in MODELS]
    data = [(name, json.loads(p.read_text())) for name, p in sources if p.is_file()]
    if not data:
        return []
    users = [r["user"] for r in data[0][1]["results"]]
    rows = []
    for user in users:
        row = [user]
        for _, d in data:
            r = next(x for x in d["results"] if x["user"] == user)
            row.append(" → ".join(num(p["mape"]) for p in r["periods"]))
        rows.append(row)
    totals = ["Proposals accepted / unseen"]
    for _, d in data:
        totals.append(f"{sum(r['accepted'] for r in d['results'])} / {sum(r['unseen'] for r in d['results'])}")
    rows.append(totals)
    return (["## 5. Budget improvement (budget error per closed period: Aug → Sep → Oct; lower is closer to spending)", "",
             "One pass per model. Accept decisions: the user approved all collected proposals as a set "
             "(see Changes in `eval/preregistration.md`). The agent is credited only with the difference from no AI.", ""]
            + table(["User", *(name for name, _ in data)], rows))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("folder", type=Path, help="one date's results folder, e.g. eval/results/2026-11-05")
    args = p.parse_args(argv)
    root = args.folder
    commits = {json.loads(c.read_text()).get("commit") for c in root.glob("*/config.json")
               if json.loads(c.read_text()).get("finished_at")}
    lines = [f"# Benchmark results, {root.name}", "", f"Commits: {', '.join(sorted(c[:7] for c in commits if c))}. "
             "Judge: `gemma3:12b`. Generated by `eval/make_tables.py`.", ""]
    for section in (models_section, modes_section, verdicts_section, variation_section, improvement_section):
        lines += section(root)
    (root / "tables.md").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
