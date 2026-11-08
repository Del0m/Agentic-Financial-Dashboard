"""Does the budget get closer to actual spending over simulated months, and how much of that is the agent's doing?

Each user in IMPROVE_USERS goes through real daily runs for 3 closed periods. Budget error is reported per period.

    --baseline   no model calls and nothing accepted, so the close logic alone sets the next budget
    --collect    a real model with nothing accepted; writes the proposals it made for Claude to decide on
    --merge      combines the collected files into eval/acceptance/proposals.json, with no model names
    --replay     a real model, applying eval/acceptance/decisions.json. a proposal with no decision counts as rejected

The agent is credited only with the difference from --baseline. Run from the repo root:
    venv/bin/python eval/budget_improvement.py --baseline
    venv/bin/python eval/budget_improvement.py --collect --model qwen2.5:14b
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from cryptography.fernet import Fernet

os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy import select

from app import config
from app.agents.llm import get_chat_model
from app.budget_engine.txns import category_totals, month_end, spending
from app.budget_service.initialize import load_txns
from app.budget_service.simulate import advance_days
from app.db.models import Alert, BudgetLine, BudgetPeriod
from eval.budget_accuracy import SCORED
from eval.run_eval import model_digests
from eval.budget_users import IMPROVE_USERS, seed_profile
from eval.fixtures import temp_db
from eval.metrics import mape

ACCEPTANCE_DIR = REPO / "eval" / "acceptance"
RESULTS_DIR = REPO / "eval" / "results"
SEED_UNTIL = date(2026, 7, 31)      # three months of history, so the first budget starts on Aug 1
LAST_DAY = date(2026, 11, 1)        # closes August, September and October


# alert ids change every run, so a decision is keyed on what was proposed
def proposal_key(user_id: str, day: str, alert: Alert) -> str:
    return f"{user_id}|{day}|{alert.category}|{alert.action}|{alert.proposed_limit_cents}"


# what a user would see in the alert, plus the spending behind it. no golden answer, planted fact, model or error
def proposal_facts(session, user_id: str, day: str, alert: Alert) -> dict:
    rows = session.execute(select(BudgetPeriod.start_date, BudgetLine.limit_cents, BudgetLine.spent_cents)
                           .join(BudgetLine, BudgetLine.period_id == BudgetPeriod.id)
                           .where(BudgetPeriod.user_id == user_id, BudgetLine.category == alert.category)
                           .order_by(BudgetPeriod.start_date)).all()
    return {"key": proposal_key(user_id, day, alert), "category": alert.category, "cause": alert.cause,
            "message": alert.message, "proposed_limit_cents": alert.proposed_limit_cents,
            "periods": [{"start": start.isoformat(), "limit_cents": limit, "spent_cents": spent}
                        for start, limit, spent in rows]}


def period_errors(session, user_id: str) -> list[dict]:
    out = []
    periods = session.scalars(select(BudgetPeriod).where(BudgetPeriod.user_id == user_id, BudgetPeriod.status == "closed")
                              .order_by(BudgetPeriod.start_date)).all()
    for period in periods:
        end = month_end(period.start_date)
        actual = category_totals(spending(load_txns(session, user_id, period.start_date, end)), period.start_date, end)
        pairs = [(ln.limit_cents, actual[ln.category]) for ln in period.lines
                 if ln.group in SCORED and actual.get(ln.category, 0) > 0]
        out.append({"start": period.start_date.isoformat(), "mape": mape(pairs), "lines": len(pairs)})
    return out


def run_user(profile, *, model, decisions: Optional[dict], audit_path: Path) -> dict:
    previous = config.PROMPT_AUDIT_PATH
    config.PROMPT_AUDIT_PATH = audit_path
    proposals, seen, applied, unseen = [], set(), {"accept": 0, "reject": 0}, 0
    started = time.perf_counter()
    try:
        with temp_db() as Session:
            with Session() as s:
                user_id = seed_profile(s, profile, until=SEED_UNTIL)

            # runs after each day, before the 1st's close can expire what's still pending
            def on_day(day: dict) -> None:
                nonlocal unseen
                with Session() as s:
                    rows = s.scalars(select(Alert).where(Alert.user_id == user_id, Alert.action == "adjust_next_budget",
                                                         Alert.status == "pending_user").order_by(Alert.id)).all()
                    for alert in rows:
                        if alert.id in seen:
                            continue
                        seen.add(alert.id)
                        proposals.append(proposal_facts(s, user_id, day["date"], alert))
                        if decisions is None:
                            continue
                        decision = decisions.get(proposal_key(user_id, day["date"], alert))
                        if decision is None:
                            unseen += 1         # left pending, so close expires it: counted as rejected
                            continue
                        applied[decision["decision"]] += 1
                        alert.status = "accepted" if decision["decision"] == "accept" else "dismissed"
                    s.commit()

            advance_days(Session, user_id, (LAST_DAY - SEED_UNTIL).days, model=model, checkpointer=None, on_day=on_day)
            with Session() as s:
                errors = period_errors(s, user_id)
    finally:
        config.PROMPT_AUDIT_PATH = previous
    return {"user": profile.id, "periods": errors, "proposals": len(proposals), "accepted": applied["accept"],
            "rejected": applied["reject"], "unseen": unseen, "seconds": round(time.perf_counter() - started, 1),
            "proposal_rows": proposals}


def merge_proposals(root: Path = ACCEPTANCE_DIR) -> dict:
    merged = {}
    for path in sorted(root.glob("collected-*.json")):
        for row in json.loads(path.read_text()):
            merged.setdefault(row["key"], row)      # one row per key, so a decision can't depend on the model
    out = sorted(merged.values(), key=lambda row: row["key"])
    (root / "proposals.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return {"proposals": len(out)}


def load_decisions(path: Path) -> dict:
    data = json.loads(path.read_text())
    for key, entry in data["decisions"].items():
        if entry.get("decision") not in ("accept", "reject"):
            raise ValueError(f"{key}: decision must be accept or reject")
    return data["decisions"]


def _safe(model: str) -> str:
    return model.replace(":", "_").replace("/", "_")


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    for flag in ("--baseline", "--collect", "--merge", "--replay"):
        mode.add_argument(flag, action="store_true")
    p.add_argument("--model", default=config.OLLAMA_MODEL)
    p.add_argument("--only", help="run just this user id")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.merge:
        print(merge_proposals())
        return 0
    name = "baseline" if args.baseline else "collect" if args.collect else "replay"
    decisions = load_decisions(ACCEPTANCE_DIR / "decisions.json") if args.replay else None
    # replies that never parse, so every flagged category falls back to a plain alert and nothing is proposed
    model = FakeListChatModel(responses=["no model"]) if args.baseline else get_chat_model(temperature=0, model=args.model)
    users = [p for p in IMPROVE_USERS if args.only in (None, p.id)]
    out = RESULTS_DIR / date.today().isoformat() / f"improvement-{name}" / ("no-ai" if args.baseline else _safe(args.model))
    out.mkdir(parents=True, exist_ok=True)
    results = [run_user(p, model=model, decisions=decisions, audit_path=out / "audit.jsonl") for p in users]
    if args.collect:
        rows = [row for r in results for row in r["proposal_rows"]]
        (ACCEPTANCE_DIR / f"collected-{_safe(args.model)}.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
    (out / "results.json").write_text(json.dumps(
        {"mode": name, "model": None if args.baseline else args.model,
         "model_digests": {} if args.baseline else model_digests([args.model]), "results": results},
        indent=2, ensure_ascii=False) + "\n")
    for r in results:
        print(r["user"], [(p["start"][:7], p["mape"]) for p in r["periods"]], "proposals", r["proposals"],
              "accepted", r["accepted"], "unseen", r["unseen"], f"{r['seconds']}s")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
