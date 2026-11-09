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

--plaid sends the purchase through Plaid Sandbox instead, for the user whose banks are linked through Plaid
(dev@localhost, the "Plaid bank" view in the app). It lands at the bank like a real card swipe, Plaid assigns its
category, and the next budget run's sync pulls it in. That needs a bank linked as Plaid's user_transactions_dynamic
test user (scripts/sandbox_link.py --dynamic, or Connect a bank in the app with that username):

    python scripts/demo_purchase.py laptop --plaid --run

With docker compose, run it in the backend container: docker compose exec backend python scripts/demo_purchase.py laptop
"""
import argparse
import calendar
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Optional
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
# make `app`, `scripts` and `eval` importable however the script is launched
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from cryptography.fernet import Fernet
from dotenv import load_dotenv

# the real key first: --plaid decrypts the linked bank's access token. app.security fails at import without a key,
# and adding a purchase to a seeded user never decrypts anything, so a throwaway key is enough there
load_dotenv(REPO / ".env")
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from sqlalchemy import select

from app.budget_service.daily import running_elsewhere, sim_prefs, sim_today, user_lock
from app.db.models import Account, AgentRun, Alert, PlaidItem, Transaction, User

DEMO_USER = "budget-overspend-recurring"
PLAID_USER_EMAIL = "dev@localhost"   # the user plaid links banks to (the app's "Plaid bank" view)
PLAID_WAIT_SECONDS = 5               # plaid takes a few seconds before a new sandbox transaction syncs
PREFIX = "demo-purchase-"         # plaid_txn_id prefix, so --undo finds exactly what this script added


@dataclass(frozen=True)
class Preset:
    merchant: str
    cents: int
    category: str
    months: int                   # 1 = a single purchase; n = the same charge on this day for n months, ending today
    shows: str
    plaid_name: str               # the description plaid sees with --plaid; it picks the category from this


PRESETS = {
    "laptop": Preset("ElectroHub", 120000, "GENERAL_MERCHANDISE", 1,
                     "a big one-off. It has no budget line of its own, so it lands in \"Everything else\" and puts "
                     "that line ahead of pace (through day 15 of the month): a one-time overspend alert",
                     "ElectroHub laptop"),
    "streaming": Preset("Streamly", 1549, "ENTERTAINMENT", 3,
                        "a subscription charged on this day for the last three months: the recurring detector "
                        "finds a new subscription and the agent decides whether to flag it", "Streamly subscription"),
    "haircut": Preset("Barber", 3500, "PERSONAL_CARE", 1,
                      "more personal care, which is already over its limit: the agent sees it already told you "
                      "this month and shouldn't nag twice", "Barber haircut"),
    "coffee": Preset("Blue Bottle Cafe", 650, "FOOD_AND_DRINK", 1,
                     "a small everyday purchase: nothing is ahead of pace, so the agent stays quiet", "Blue Bottle Cafe"),
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


class PlaidPushError(Exception):
    pass


# the user's real (not seeded) linked banks, newest first
def plaid_items(session, user_id: str) -> list[PlaidItem]:
    from app.budget_service.alerts import SEED_TOKEN
    return list(session.scalars(select(PlaidItem).where(
        PlaidItem.user_id == user_id, PlaidItem.status == "active", PlaidItem.access_token_enc != SEED_TOKEN)
        .order_by(PlaidItem.id.desc())))


# sends the purchase to plaid sandbox, on the first linked bank that takes new transactions (only one linked as
# user_transactions_dynamic does). one call for every date, since plaid rate-limits this endpoint. returns the bank's
# row id and name. nothing is written here: the app's own sync brings it in, as it would a real card swipe
def push_to_plaid(session_factory, user_id: str, name: str, cents: int, days: list[date]) -> tuple[int, Optional[str]]:
    from app.plaid_service.client import PlaidError, plaid_post
    from app.security import decrypt
    with session_factory() as s:
        banks = [(i.id, i.institution_name, i.access_token_enc) for i in plaid_items(s, user_id)]
    if not banks:
        raise PlaidPushError(f"{user_id} has no bank linked through Plaid. Link one first: "
                             "python scripts/sandbox_link.py --dynamic")
    txns = [{"date_transacted": d.isoformat(), "date_posted": d.isoformat(), "amount": cents / 100,
             "description": name, "iso_currency_code": "USD"} for d in days]
    refused = []
    for item_id, institution, token_enc in banks:
        try:
            plaid_post("/sandbox/transactions/create", {"access_token": decrypt(token_enc), "transactions": txns})
            return item_id, institution
        except PlaidError as exc:
            if exc.error_code == "SANDBOX_TRANSACTIONS_CREATE_LIMIT":
                raise PlaidPushError("Plaid's rate limit for new Sandbox transactions was hit; wait a minute and "
                                     "try again") from exc
            refused.append(f"bank {item_id}: {exc.error_code}")
        except Exception as exc:          # a token this key can't decrypt, plaid unreachable: try the next bank
            refused.append(f"bank {item_id}: {type(exc).__name__}")
    raise PlaidPushError("none of the linked banks takes new Sandbox transactions (" + "; ".join(refused) + "). "
                         "Only a bank linked as user_transactions_dynamic does: python scripts/sandbox_link.py --dynamic")


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
        summary = s.get(AgentRun, state["run_id"]).summary_json or {}
        alerts = {a.id: a for a in s.scalars(select(Alert).where(Alert.id.in_(ids)))}
    sync = summary.get("sync") or {}
    if sync and not sync.get("skipped"):
        failed = f", {len(sync['errors'])} unreachable" if sync.get("errors") else ""
        print(f"  [plaid]  synced {sync.get('added', 0)} new transaction(s) from {sync.get('items', 0)} linked bank(s){failed}")
    print_trace(summary.get("trace") or {}, alerts)
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
    parser.add_argument("--user", help=f"user id; default {DEMO_USER}, or with --plaid the user {PLAID_USER_EMAIL}")
    parser.add_argument("--plaid", action="store_true",
                        help="send it through Plaid Sandbox to the user's linked bank; Plaid picks the category")
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
        user_id = args.user or (s.scalar(select(User.id).where(User.email == PLAID_USER_EMAIL)) if args.plaid
                                else DEMO_USER)
        if user_id is None or s.get(User, user_id) is None:
            print(f"no user {args.user or PLAID_USER_EMAIL}; " + ("link a bank first (scripts/sandbox_link.py --dynamic)"
                  if args.plaid else "seed it first (see scripts/setup_demo.py)"))
            return 1
        # a plaid user lives on real dates; a seeded one on its simulated today
        today = args.date or (None if args.plaid else sim_today(s, user_id)) or date.today()
    if args.undo:
        if args.plaid:
            print("Plaid Sandbox can't take a transaction back. To start clean, unlink the bank and link it again.")
            return 1
        print(f"removed {undo(session_factory, user_id)} purchase(s) from {user_id}")
        return 0
    preset = PRESETS.get(args.preset)
    merchant = args.merchant or (preset and preset.merchant)
    cents = round(args.amount * 100) if args.amount is not None else (preset and preset.cents)
    category = (args.category or (preset and preset.category) or "").upper()
    months = args.months or (preset.months if preset else 1)
    if not (merchant and cents and (category or args.plaid)):
        parser.error("pick a preset, or give --merchant, --amount and --category")
    if cents <= 0 or months < 1:
        parser.error("--amount must be positive and --months at least 1")
    custom = bool(args.merchant or args.amount or args.category)
    if args.plaid:
        name = preset.plaid_name if preset and not args.merchant else merchant
        days = sorted(_months_back(today, n) for n in range(months))
        try:
            item_id, institution = push_to_plaid(session_factory, user_id, name, cents, days)
        except PlaidPushError as exc:
            print(exc)
            return 1
        dates = ", ".join(d.isoformat() for d in days)
        print(f"sent to Plaid Sandbox: {name} {_money(cents)} on {dates}, to bank {item_id} ({institution or 'Sandbox'})")
        print("Plaid assigns its category; the next budget run's sync pulls it in like any bank transaction")
        if args.category:
            print("(--category is ignored with --plaid: Plaid categorizes it)")
    else:
        days = add(session_factory, user_id, merchant, cents, category, today, months)
        dates = ", ".join(d.isoformat() for d in days)
        print(f"added {merchant} {_money(cents)} ({category}) on {dates} for {user_id}")
    if preset and not custom:
        print(f"what it shows: {preset.shows}")
    if args.run:
        if args.plaid:
            print(f"waiting {PLAID_WAIT_SECONDS} s for Plaid to post it...")
            time.sleep(PLAID_WAIT_SECONDS)
        code = run(session_factory, user_id, today)
        if args.plaid:
            print_synced(session_factory, user_id, cents, days)
        return code
    print("next: click Run budget check on the Alerts page" + ("" if args.plaid else " (or Advance 1 day)")
          + ", then refresh the dashboard")
    return 0


# with --plaid: the purchase as the run's sync stored it, with the category plaid gave it
def print_synced(session_factory, user_id: str, cents: int, days: list[date]) -> None:
    with session_factory() as s:
        rows = list(s.scalars(select(Transaction).where(
            Transaction.user_id == user_id, Transaction.amount_cents == cents, Transaction.date.in_(days),
            Transaction.plaid_txn_id.not_like(PREFIX + "%")).order_by(Transaction.id.desc())))
    if not rows:
        print("Plaid hasn't sent it yet; click Run budget check in a few seconds")
    for t in rows[:len(days)]:
        print(f"synced from Plaid: {t.merchant_name or 'purchase'} {_money(t.amount_cents)} on {t.date}, "
              f"Plaid's category {t.pf_category_primary}")


if __name__ == "__main__":
    sys.exit(main())
