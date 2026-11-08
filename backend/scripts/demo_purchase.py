"""Drop a purchase into a simulated user's transactions mid-demo, so the agents have something new to react to.

The purchase is dated on the user's simulated "today" (or --date), lands like a transaction from the bank feed,
and is picked up by the next budget run: click **Run budget check** on the Alerts page, or pass --run to run the
budget agent from here. Presets are tuned for the demo user (budget-overspend-recurring):

    python scripts/demo_purchase.py --list
    python scripts/demo_purchase.py laptop                 # a $1,200 one-off: "Everything else" runs ahead of pace
    python scripts/demo_purchase.py streaming              # $15.49 a month for three months: a new subscription
    python scripts/demo_purchase.py haircut                # personal care is already over: the agent remembers
    python scripts/demo_purchase.py coffee                 # a small purchase: nothing to flag
    python scripts/demo_purchase.py --merchant "Sushi Go" --amount 64.20 --category FOOD_AND_DRINK
    python scripts/demo_purchase.py --undo                 # remove every purchase this script added

With docker compose, run it in the backend container: docker compose exec backend python scripts/demo_purchase.py laptop
"""
import argparse
import calendar
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# make `app`, `scripts` and `eval` importable however the script is launched
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from cryptography.fernet import Fernet

# app.security fails at import without a key; adding a purchase never decrypts anything
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select

from app.budget_service.daily import running_elsewhere, sim_prefs, sim_today, user_lock
from app.db.models import Account, AgentRun, Alert, Transaction, User

DEMO_USER = "budget-overspend-recurring"
PREFIX = "demo-purchase-"         # plaid_txn_id prefix, so --undo finds exactly what this script added


@dataclass(frozen=True)
class Preset:
    merchant: str
    cents: int
    category: str
    months: int                   # 1 = a single purchase; n = the same charge on this day for n months, ending today
    shows: str


PRESETS = {
    "laptop": Preset("ElectroHub", 120000, "GENERAL_MERCHANDISE", 1,
                     "a big one-off. It has no budget line of its own, so it lands in \"Everything else\" and puts "
                     "that line ahead of pace (through day 15 of the month): a one-time overspend alert"),
    "streaming": Preset("Streamly", 1549, "ENTERTAINMENT", 3,
                        "a subscription charged on this day for the last three months: the recurring detector "
                        "finds a new subscription and the agent decides whether to flag it"),
    "haircut": Preset("Barber", 3500, "PERSONAL_CARE", 1,
                      "more personal care, which is already over its limit: the agent sees it already told you "
                      "this month and shouldn't nag twice"),
    "coffee": Preset("Blue Bottle Cafe", 650, "FOOD_AND_DRINK", 1,
                     "a small everyday purchase: nothing is ahead of pace, so the agent stays quiet"),
}


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


# the same day of the month, n months back (the 31st becomes the month's last day)
def _months_back(day: date, n: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - n, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# a profile with an opening balance has one that moves with its transactions, as in the simulation
def _moves_balance(sim: dict) -> bool:
    from eval import budget_users
    return budget_users.profile_by_id(sim["profile"]).opening_cents is not None


def add(session_factory, user_id: str, merchant: str, cents: int, category: str, today: date, months: int) -> list[date]:
    with session_factory() as s:
        sim = sim_prefs(s, user_id)
        account = s.scalar(select(Account).where(Account.user_id == user_id).order_by(Account.id).limit(1))
        if account is None:
            raise SystemExit(f"{user_id} has no account to add a purchase to")
        moves = sim is not None and _moves_balance(sim)
        days = sorted(_months_back(today, n) for n in range(months))
        for day in days:
            s.add(Transaction(user_id=user_id, account_id=account.id, plaid_txn_id=PREFIX + uuid.uuid4().hex[:12],
                              date=day, amount_cents=cents, merchant_name=merchant, pf_category_primary=category))
            if moves:
                account.current_balance_cents -= cents
                account.available_balance_cents -= cents
        s.commit()
    return days


def undo(session_factory, user_id: str) -> int:
    with session_factory() as s:
        sim = sim_prefs(s, user_id)
        moves = sim is not None and _moves_balance(sim)
        rows = list(s.scalars(select(Transaction).where(Transaction.user_id == user_id,
                                                        Transaction.plaid_txn_id.like(PREFIX + "%"))))
        for row in rows:
            if moves:
                row.account.current_balance_cents += row.amount_cents
                row.account.available_balance_cents += row.amount_cents
            s.delete(row)
        s.commit()
    return len(rows)


# the budget agent, run here the way a simulated day runs it; prints what it flagged and the alerts it wrote
def run(session_factory, user_id: str, today: date) -> int:
    from app.agents.llm import get_chat_model
    from app.api import budget_hooks
    from app.budget_service.daily import run_daily_locked
    lock = user_lock(user_id)
    if running_elsewhere(session_factory, user_id) or not lock.acquire(blocking=False, session_factory=session_factory):
        print("A budget run is already in progress; try again when it finishes.")
        return 1
    print(f"running the budget agent for {today} (about 20 seconds to a minute)...")
    try:
        state = run_daily_locked(session_factory, user_id, today, model=get_chat_model(),
                                 checkpointer=budget_hooks.get_checkpointer())
    finally:
        lock.release()
    print(f"run {state['run_id']}: {state['status']}")
    ids = state.get("alert_ids") or []
    with session_factory() as s:
        trace = (s.get(AgentRun, state["run_id"]).summary_json or {}).get("trace") or {}
        alerts = {a.id: a for a in s.scalars(select(Alert).where(Alert.id.in_(ids)))}
    print_trace(trace, alerts)
    print("the same trace is under Agent activity on the Alerts page")
    return 0


def _line(category: str) -> str:
    return "Everything else" if category == "FREE" else category.replace("_", " ").capitalize()


# the run's decision trace, step by step, with who did each step
def print_trace(trace: dict, alerts: dict) -> None:
    flags, signals = trace.get("flags") or [], trace.get("signals") or []
    if not flags:
        print(f"  [code]   checked {trace.get('lines_checked', 0)} budget lines: nothing ahead of pace, model not asked")
    for f in flags:
        print(f"\n  {_line(f['category'])} ({'over limit' if f['reason'] == 'over_limit' else 'ahead of pace'})")
        if f.get("spent_cents") is not None:
            pace = f" · {f['pace']:.2f}x pace" if f.get("pace") is not None else ""
            print(f"    [code]   measured {_money(f['spent_cents'])} of {_money(f['limit_cents'])} by day "
                  f"{f.get('days_elapsed')} of {f.get('days_in_period')}{pace}")
        earlier = f.get("earlier_alerts") or []
        print(f"    [memory] {len(earlier)} earlier alert(s) on this line this month"
              + (f"; past advice: {', '.join(p['action'] + '/' + str(p['outcome']) for p in f['past_advice'])}"
                 if f.get("past_advice") else ""))
        print(f"    [model]  cause: {f.get('cause') or 'no answer'}" + (f" - {f['evidence']}" if f.get("evidence") else ""))
        print(f"    [model]  chose: {f.get('model_action') or 'no answer'}")
        print(f"    [code]   check: {f.get('override') or 'allowed'} -> {f.get('action')}")
        alert = alerts.get(f.get("alert_id"))
        print(f"    result:  alert {alert.id}: {alert.message}" if alert else "    result:  stayed quiet, no new alert")
    for sig in signals:
        who = "code (no model answer)" if sig.get("fallback") else f"model chose {sig.get('model_action')}"
        alert = alerts.get(sig.get("alert_id"))
        print(f"\n  signal {sig['kind']}: {who}"
              + (f"\n    result:  alert {alert.id}: {alert.message}" if alert else "\n    result:  already told you"))


def main(argv=None, session_factory=None) -> int:
    parser = argparse.ArgumentParser(description="Add a purchase to a simulated user mid-demo.")
    parser.add_argument("preset", nargs="?", choices=sorted(PRESETS), help="a ready-made purchase; see --list")
    parser.add_argument("--merchant")
    parser.add_argument("--amount", type=float, help="dollars, e.g. 64.20")
    parser.add_argument("--category", help="Plaid primary category, e.g. FOOD_AND_DRINK, GENERAL_MERCHANDISE")
    parser.add_argument("--months", type=int, help="charge it on this day for this many months, ending today")
    parser.add_argument("--date", type=date.fromisoformat, help="YYYY-MM-DD; default: the user's simulated today")
    parser.add_argument("--user", default=DEMO_USER)
    parser.add_argument("--run", action="store_true", help="run the budget agent now and print what it did")
    parser.add_argument("--undo", action="store_true", help="remove every purchase this script added")
    parser.add_argument("--list", action="store_true", help="show the presets")
    args = parser.parse_args(argv)

    if args.list:
        for name, p in PRESETS.items():
            when = f" a month for {p.months} months" if p.months > 1 else ""
            print(f"{name:10} {p.merchant}, {_money(p.cents)}{when} ({p.category})\n{'':10} {p.shows}\n")
        return 0
    if session_factory is None:
        from app.db.session import SessionLocal
        session_factory = SessionLocal
    with session_factory() as s:
        if s.get(User, args.user) is None:
            print(f"no user {args.user}; seed it first (see scripts/setup_demo.py)")
            return 1
        today = args.date or sim_today(s, args.user) or date.today()
    if args.undo:
        print(f"removed {undo(session_factory, args.user)} purchase(s) from {args.user}")
        return 0

    preset = PRESETS.get(args.preset)
    merchant = args.merchant or (preset and preset.merchant)
    cents = round(args.amount * 100) if args.amount is not None else (preset and preset.cents)
    category = (args.category or (preset and preset.category) or "").upper()
    months = args.months or (preset.months if preset else 1)
    if not (merchant and cents and category):
        parser.error("pick a preset, or give --merchant, --amount and --category")
    if cents <= 0 or months < 1:
        parser.error("--amount must be positive and --months at least 1")

    days = add(session_factory, args.user, merchant, cents, category, today, months)
    dates = ", ".join(d.isoformat() for d in days)
    print(f"added {merchant} {_money(cents)} ({category}) on {dates} for {args.user}")
    if preset and not (args.merchant or args.amount or args.category):
        print(f"what it shows: {preset.shows}")
    if args.run:
        return run(session_factory, args.user, today)
    print("next: click Run budget check on the Alerts page (or Advance 1 day), then refresh the dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(main())
