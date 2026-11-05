"""Pick the hand-read sample of chat answers, with conditions hidden, and report how often the reader and judge agree.

Run from the repo root:
    venv/bin/python eval/sample_answers.py pick <results folder>... --out eval/results/<date>/hand_read
Fill in each "passes" with true or false in sample.json, without opening key.json. Then:
    venv/bin/python eval/sample_answers.py score eval/results/<date>/hand_read
"""
import argparse
import json
import random
import sys
from pathlib import Path

SAMPLE_SIZE = 30
SEED = 27


def chat_runs(folder: Path) -> list[dict]:
    out = []
    for path in sorted(folder.glob("*.run*.json")):
        item = json.loads(path.read_text())
        if item["record"].get("kind", "chat") == "chat" and item["record"].get("answer"):
            out.append({"folder": folder.name, "path": path.name, "scenario": item["record"]["scenario_id"],
                        "answer": item["record"]["answer"], "judge_passed": item["scores"]["judge_passed"]})
    return out


# a round across the conditions, so each is represented, then shuffled so the order doesn't give a condition away
def pick(folders: list[Path], size: int = SAMPLE_SIZE, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    pools = [chat_runs(f) for f in folders]
    for pool in pools:
        rng.shuffle(pool)
    chosen = []
    while len(chosen) < size and any(pools):
        for pool in pools:
            if pool and len(chosen) < size:
                chosen.append(pool.pop())
    rng.shuffle(chosen)
    return chosen


def write_sample(chosen: list[dict], out: Path, questions: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    sample = [{"n": n, "question": questions.get(c["scenario"], ""), "answer": c["answer"], "passes": None}
              for n, c in enumerate(chosen, 1)]
    (out / "sample.json").write_text(json.dumps(sample, indent=2, ensure_ascii=False) + "\n")
    key = [{"n": n, **{k: c[k] for k in ("folder", "path", "scenario", "judge_passed")}} for n, c in enumerate(chosen, 1)]
    (out / "key.json").write_text(json.dumps(key, indent=2) + "\n")


def score(out: Path) -> dict:
    sample = json.loads((out / "sample.json").read_text())
    key = {row["n"]: row for row in json.loads((out / "key.json").read_text())}
    rated = [row for row in sample if row["passes"] is not None]
    agreed = sum(1 for row in rated if row["passes"] == key[row["n"]]["judge_passed"])
    return {"rated": len(rated), "unrated": len(sample) - len(rated), "agreed": agreed,
            "agreement": agreed / len(rated) if rated else None,
            "disagreements": [key[r["n"]]["scenario"] for r in rated if r["passes"] != key[r["n"]]["judge_passed"]]}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    pick_p = sub.add_parser("pick")
    pick_p.add_argument("folders", nargs="+", type=Path)
    pick_p.add_argument("--out", type=Path, required=True)
    score_p = sub.add_parser("score")
    score_p.add_argument("out", type=Path)
    args = p.parse_args(argv)
    if args.command == "score":
        print(json.dumps(score(args.out), indent=2))
        return 0
    repo = Path(__file__).resolve().parent.parent
    questions = {}
    for path in (repo / "eval" / "scenarios").glob("*.json"):
        data = json.loads(path.read_text())
        questions[data["id"]] = data.get("question", "")
    write_sample(pick(args.folders), args.out, questions)
    print(f"Wrote {args.out / 'sample.json'}. Don't open key.json until you've rated them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
