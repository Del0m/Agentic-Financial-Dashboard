"""Compare two results folders scenario by scenario, and apply the verdict rule from eval/preregistration.md.

Run from the repo root:
    venv/bin/python eval/compare.py <with-technology folder> <without-technology folder> --metric chat
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent

GROUPS = ("temporal", "graph_suited", "graph_unsuited")
ALL = "all"
HELPS, HURTS, NO_HELP, UNINFORMATIVE = 2, -2, "does not help", "uninformative"
MIN_FIRE_RATE = 0.05


class NotComparable(ValueError):
    pass


def chat_passed(item: dict) -> bool:
    return bool(item["scores"]["facts"]["passed"])


def budget_passed(item: dict) -> bool:
    checks = item["scores"]["checks"]
    wanted = [checks[name] for name in ("cause", "action") if name in checks]
    return all(wanted) if wanted else bool(item["scores"]["passed"])


def agent_passed(item: dict) -> bool:
    return bool(item["scores"]["passed"])


METRICS: dict[str, tuple[Callable[[dict], bool], str]] = {
    "chat": (chat_passed, "chat"), "budget": (budget_passed, "budget"), "retries": (agent_passed, "agent"),
}


def load_runs(folder: Path) -> tuple[dict, dict[str, list[dict]]]:
    config = json.loads((folder / "config.json").read_text())
    runs: dict[str, list[dict]] = {}
    for path in sorted(folder.glob("*.run*.json")):
        item = json.loads(path.read_text())
        runs.setdefault(item["record"]["scenario_id"], []).append(item)
    return config, runs


def _kind(items: list[dict]) -> str:
    return items[0]["record"].get("kind", "chat")


def _wanted(kind: str, family: str) -> bool:
    return kind == family if family != "agent" else kind in ("budget", "goal")


# passes on at least 2 of 3 runs. a single run counts by itself
def scenario_passes(items: list[dict], passed: Callable[[dict], bool]) -> bool:
    return sum(1 for i in items if passed(i)) * 2 > len(items)


def scenario_group(tags: tuple[str, ...]) -> Optional[str]:
    return next((g for g in GROUPS if g in tags), None)


def verdict(wins: int, losses: int) -> str:
    net = wins - losses
    return "helps" if net >= HELPS else "hurts" if net <= HURTS else NO_HELP


# resends and goal corrections per model call, from the condition where they were on
def fire_rates(runs: dict[str, list[dict]]) -> dict[str, Optional[float]]:
    calls = resends = goal_calls = corrections = 0
    for items in runs.values():
        for item in items:
            record = item["record"]
            if record.get("kind") == "budget":
                calls += record.get("llm_calls", 0)
                resends += record.get("llm_retries", 0)
            elif record.get("kind") == "goal":
                goal_calls += record.get("attempts", 0)
                corrections += max(record.get("attempts", 0) - 1, 0)
    return {"resend": resends / calls if calls else None, "goal_correction": corrections / goal_calls if goal_calls else None}


def compare(with_folder: Path, without_folder: Path, metric: str, tags: dict[str, tuple[str, ...]]) -> dict:
    passed, family = METRICS[metric]
    with_cfg, with_runs = load_runs(with_folder)
    without_cfg, without_runs = load_runs(without_folder)
    if with_cfg.get("commit") != without_cfg.get("commit"):
        raise NotComparable(f"commit differs: {with_cfg.get('commit')} vs {without_cfg.get('commit')}")
    kinds = ({_kind(items) for items in with_runs.values()} &
             {_kind(items) for items in without_runs.values()})
    kinds = sorted(kind for kind in kinds if _wanted(kind, family))
    if not kinds:
        raise NotComparable("no scenarios in common for this metric")
    shared = []
    for kind in kinds:
        with_ids = {sid for sid, items in with_runs.items() if _kind(items) == kind}
        without_ids = {sid for sid, items in without_runs.items() if _kind(items) == kind}
        if with_ids != without_ids:
            raise NotComparable(f"{kind} scenario sets differ: only with={sorted(with_ids - without_ids)}, "
                                f"only without={sorted(without_ids - with_ids)}")
        shared.extend(with_ids)
    with_hashes, without_hashes = (cfg.get("scenario_hashes_by_kind") for cfg in (with_cfg, without_cfg))
    if with_hashes is not None and without_hashes is not None:
        for kind in kinds:
            if not with_hashes.get(kind) or not without_hashes.get(kind):
                raise NotComparable(f"scenario_hashes_by_kind missing {kind}")
            if with_hashes[kind] != without_hashes[kind]:
                raise NotComparable(f"{kind} scenario_hash differs: {with_hashes[kind]} vs {without_hashes[kind]}")
    elif not with_cfg.get("scenario_hash") or with_cfg.get("scenario_hash") != without_cfg.get("scenario_hash"):
        # Old results have only a whole-run hash; never infer missing provenance from today's scenario files.
        raise NotComparable("scenario_hash differs or is missing; subset comparisons need runs with "
                            "scenario_hashes_by_kind")
    shared.sort()
    rows = []
    for sid in shared:
        a, b = scenario_passes(with_runs[sid], passed), scenario_passes(without_runs[sid], passed)
        result = "win" if a and not b else "loss" if b and not a else "tie"
        rows.append({"scenario": sid, "group": scenario_group(tags.get(sid, ())), "with": a, "without": b, "result": result})
    groups = {}
    for name in (ALL, *GROUPS):
        members = [r for r in rows if name == ALL or r["group"] == name]
        if not members:
            continue
        wins, losses = [sum(1 for r in members if r["result"] == kind) for kind in ("win", "loss")]
        groups[name] = {"scenarios": len(members), "wins": wins, "losses": losses, "ties": len(members) - wins - losses,
                        "passed_with": sum(1 for r in members if r["with"]),
                        "passed_without": sum(1 for r in members if r["without"]), "verdict": verdict(wins, losses)}
    out = {"with": with_folder.name, "without": without_folder.name, "metric": metric, "commit": with_cfg.get("commit"),
           "groups": groups, "scenarios": rows}
    if metric == "retries":
        rates = fire_rates({sid: with_runs[sid] for sid in shared})
        out["fire_rates"] = rates
        out["uninformative"] = {name: rate is None or rate < MIN_FIRE_RATE for name, rate in rates.items()}
    return out


def to_markdown(result: dict) -> str:
    lines = [f"# {result['with']} vs {result['without']} ({result['metric']})", "",
             "| Group | Scenarios | Passed with | Passed without | Wins | Losses | Ties | Verdict |",
             "|---|---|---|---|---|---|---|---|"]
    for name, g in result["groups"].items():
        lines.append(f"| {name} | {g['scenarios']} | {g['passed_with']} | {g['passed_without']} | {g['wins']} | "
                     f"{g['losses']} | {g['ties']} | {g['verdict']} |")
    changed = [r for r in result["scenarios"] if r["result"] != "tie"]
    if changed:
        lines += ["", "| Scenario | Group | Result |", "|---|---|---|"]
        lines += [f"| {r['scenario']} | {r['group'] or '-'} | {r['result']} |" for r in changed]
    if "fire_rates" in result:
        lines += ["", "| Mechanism | Fire rate | Uninformative |", "|---|---|---|"]
        lines += [f"| {name} | {'-' if rate is None else f'{rate:.1%}'} | {result['uninformative'][name]} |"
                  for name, rate in result["fire_rates"].items()]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("with_folder", type=Path)
    p.add_argument("without_folder", type=Path)
    p.add_argument("--metric", choices=tuple(METRICS), default="chat")
    p.add_argument("--out", type=Path, help="write <out>.md and <out>.json here")
    args = p.parse_args(argv)
    sys.path.insert(0, str(REPO / "backend"))
    sys.path.insert(0, str(REPO))
    tags = {}
    for path in (REPO / "eval" / "scenarios").glob("*.json"):
        data = json.loads(path.read_text())
        tags[data["id"]] = tuple(data.get("tags", ()))
    try:
        result = compare(args.with_folder, args.without_folder, args.metric, tags)
    except NotComparable as exc:
        print(f"Can't compare: {exc}")
        return 2
    print(to_markdown(result))
    if args.out:
        args.out.with_suffix(".md").write_text(to_markdown(result))
        args.out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
