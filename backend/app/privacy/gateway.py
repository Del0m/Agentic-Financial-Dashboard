import json
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional, Sequence
from sqlalchemy import select
from sqlalchemy.orm import Session
from .. import config
from ..budget_engine.diagnose import OverspendFacts
from ..budget_engine.goals import GoalAllocation, GoalFacts, GoalIssue, planned_debts
from ..budget_engine.payoff import Debt, Plan, compare
from ..budget_engine.signals import Signal, allowed_actions
from ..budget_engine.txns import Txn, add_months, month_start, spending
from ..budget_service.debts import load_debts
from ..db.models import Account, Liability, Transaction
from ..finance import avalanche_key
from .scrub import scrub_account_name, scrub_merchant, scrub_note

# the only code path that turns user data into LLM prompt text (architecture section 7).
# rows are built field by field from these allowlists, never by serializing a model object,
# so a column added later can't leak by accident.

Purpose = Literal["accounts", "debts", "spending", "diagnose", "debt_plan", "goals", "recurring"]

ACCOUNT_FIELDS = ("name", "type", "subtype", "balance", "available", "limit")
DEBT_FIELDS = ("name", "kind", "apr", "balance", "min_payment", "next_due_date")
TXN_FIELDS = ("date", "amount", "merchant")
RECURRING_FIELDS = ("merchant", "category", "frequency", "typical", "last", "previous", "next_date")
TXN_LIMIT = 50
# the budget graph's diagnose step: one flagged category's facts and its rows (spec: tpc-19)
DIAGNOSE_FIELDS = ("category", "reason", "kind", "month", "limit", "spent", "pace", "day", "days_in_month",
                   "projected", "largest", "merchants", "history", "outlier_months", "transactions",
                   "this_month_alerts", "past_advice")
# the agent's memory of a category (tpc-20): what it already alerted and what the user decided, never the text
ALERT_MEMORY_FIELDS = ("severity", "action", "status")
ADVICE_MEMORY_FIELDS = ("month", "action", "outcome")
TXN_LIMIT_DIAGNOSE = 20
DEFAULT_SPENDING_DAYS = 30
EXCLUDED_PRIMARY = frozenset({"INCOME", "TRANSFER_IN", "TRANSFER_OUT"})
CARD_PAYMENT = "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT"
PSEUDONYM = re.compile(r"user_[0-9a-f]{4}")
# the payoff planner (tpc-32): only computed results go out, so the model never does the amortization itself
STRATEGIES = ("avalanche", "snowball")
EXCLUDED_REASONS = {"no_apr": "no APR on file", "no_minimum": "no minimum payment on file",
                    "mortgage": "mortgage (left out)"}
# the goal graph (tpc-23): the extras it shows the model, as shares of the cap, so it can see what paying more does
GOAL_PLAN_SHARES = (0.25, 0.5, 0.75)
GOAL_ASSUMPTIONS = ("monthly_surplus is what's left after minimum payments. Debt plans charge interest monthly at "
                    "APR/12 and hold minimum payments at today's amounts.")
PLAN_ASSUMPTIONS = ("Interest is charged monthly at APR/12; minimum payments are held at today's amounts; "
                    "not financial advice.")
_audit_lock = threading.Lock()


def _today() -> date:
    return date.today()


# cents -> dollars so the model doesn't read 41000 as forty-one thousand dollars
def _dollars(cents: Optional[int]) -> Optional[float]:
    return None if cents is None else round(cents / 100, 2)


def _month_name(d: date) -> str:
    return d.strftime("%B %Y")


class PrivacyGateway:
    # one instance per chat session: the pseudonym lives (and dies) with it
    # a chat session spans many requests, each with its own db session, so the caller passes
    # the session's pseudonym back in to keep it stable; omitted = start a new one
    def __init__(self, session: Session, user_id: str, audit_path: Optional[Path] = None,
                 pseudonym: Optional[str] = None):
        if pseudonym is not None and not PSEUDONYM.fullmatch(pseudonym):
            raise ValueError("pseudonym must match user_[0-9a-f]{4}")
        self._session = session
        self._user_id = user_id
        self._audit_path = audit_path or config.PROMPT_AUDIT_PATH
        self.pseudonym = pseudonym or "user_" + secrets.token_hex(2)
        self._goal_context: Optional[str] = None
        self._goal_cap_cents: Optional[int] = None

    # this user's "today": a simulated user's chat follows the simulation, like its dashboard.
    # imported here because budget_service.daily imports the budget graph, which imports this module
    def today(self) -> date:
        from ..budget_service.daily import sim_today
        return sim_today(self._session, self._user_id) or _today()

    def build_context(self, purpose: Purpose, *, start: Optional[date] = None,
                      end: Optional[date] = None, category: Optional[str] = None,
                      facts: Optional[OverspendFacts] = None, transactions: Optional[list[Txn]] = None,
                      this_month_alerts: Sequence[dict] = (), past_advice: Sequence[dict] = (),
                      extra_monthly_cents: Optional[int] = None, strategy: Optional[str] = None,
                      goal_facts: Optional[GoalFacts] = None,
                      goal_debts: Optional[Sequence[Debt]] = None,
                      signal: Optional[Signal] = None) -> str:
        if purpose == "diagnose":
            data = self._diagnose(facts, transactions or [])
            data["this_month_alerts"] = [{k: a.get(k) for k in ALERT_MEMORY_FIELDS} for a in this_month_alerts]
            data["past_advice"] = [{k: a.get(k) for k in ADVICE_MEMORY_FIELDS} for a in past_advice]
            category = facts.category
        elif purpose == "accounts":
            data = self._accounts()
        elif purpose == "debts":
            data = self._debts()
        elif purpose == "spending":
            data = self._spending(start, end, category)
        elif purpose == "debt_plan":
            data = self._debt_plan(extra_monthly_cents or 0, strategy)
        elif purpose == "goals":
            data = self._goals(goal_facts, goal_debts)
        elif purpose == "recurring":
            data = self._recurring(signal)
        else:
            raise ValueError(f"unknown purpose: {purpose}")

        prompt = json.dumps({"user": self.pseudonym, "purpose": purpose, "data": data},
                            separators=(",", ":"), default=str, ensure_ascii=False)
        params = {k: v for k, v in (("start", start), ("end", end), ("category", category),
                                    ("extra_monthly_cents", extra_monthly_cents), ("strategy", strategy),
                                    ("cap_cents", goal_facts.cap_cents if goal_facts else None))
                  if v is not None}
        self._audit(purpose, params, prompt)
        if purpose == "goals":
            self._goal_context = prompt
            self._goal_cap_cents = goal_facts.cap_cents
        return prompt

    def _recurring(self, signal: Signal) -> dict:
        n = signal.numbers
        if n.get("direction") == "in":
            row = {"category": "INCOME", "frequency": n["frequency"], "typical": _dollars(n["typical_cents"])}
        else:
            row = {"merchant": scrub_merchant(n.get("merchant")), "category": signal.category,
                   "frequency": n["frequency"], "typical": _dollars(n["typical_cents"]),
                   "last": _dollars(n["last_cents"]), "previous": _dollars(n.get("previous_cents")),
                   "next_date": n.get("next_date")}
        return {"kind": signal.kind, "streams": [row], "allowed_actions": list(allowed_actions(signal))}

    def goal_correction(self, allocations: Sequence[GoalAllocation], issues: Sequence[GoalIssue]) -> str:
        if self._goal_context is None:
            raise ValueError('build the goal context before a correction')
        payload = json.loads(self._goal_context)
        # Only normalized fields and code-generated reasons; never echo the rejected rationale or raw reply.
        payload['correction'] = {
            'attempt': 2,
            'rejected_goals': [{'kind': a.kind, 'monthly_dollars': _dollars(a.monthly_cents),
                                'strategy': a.strategy} for a in allocations],
            'failures': [{'code': i.code, 'index': i.index, 'kind': i.kind, 'message': i.message} for i in issues],
        }
        prompt = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        self._audit('goals', {'cap_cents': self._goal_cap_cents, 'attempt': 2}, prompt)
        return prompt

    def _accounts(self) -> list[dict]:
        rows = self._session.scalars(
            select(Account).where(Account.user_id == self._user_id).order_by(Account.name)
        )
        return [{
            "name": scrub_account_name(a.name), "type": a.type, "subtype": a.subtype,
            "balance": _dollars(a.current_balance_cents), "available": _dollars(a.available_balance_cents),
            "limit": _dollars(a.limit_cents),
        } for a in rows]

    def _debts(self) -> list[dict]:
        rows = self._session.execute(
            select(Liability, Account)
            .join(Account, Liability.account_id == Account.id)
            .where(Liability.user_id == self._user_id)
        ).all()
        debts = [{
            "name": scrub_account_name(a.name), "kind": l.kind, "apr": l.apr, "balance": _dollars(a.current_balance_cents),
            "min_payment": _dollars(l.min_payment_cents), "next_due_date": l.next_due_date,
        } for l, a in rows]
        # same avalanche order as GET /dashboard (api/dashboard.py): highest APR first, unknown last
        debts.sort(key=lambda d: avalanche_key(d["apr"], d["balance"]))
        return debts

    def _debt_plan(self, extra_monthly_cents: int, strategy: Optional[str]) -> dict:
        if strategy is not None and strategy not in STRATEGIES:
            raise ValueError(f"unknown strategy: {strategy}")
        debts = load_debts(self._session, self._user_id)
        start = add_months(month_start(_today()), 1)
        plans = compare(debts, extra_monthly_cents, start=start)
        names = {d.key: scrub_account_name(d.name) for d in debts}
        month = lambda d: d.strftime("%Y-%m") if d else None
        # an unpaid debt's interest summed over 600 months runs to billions: there's no total to quote
        interest = lambda p: _dollars(p.total_interest_cents) if p.months is not None else None

        def plan(p: Plan, base: Plan) -> dict:
            ends = p.months is not None and base.months is not None
            return {
                "strategy": p.strategy, "months": p.months, "debt_free": month(p.debt_free),
                "total_interest": interest(p),
                "order": [{"name": names[o.key], "paid_off": month(o.date), "interest": _dollars(o.interest_cents)}
                          for o in p.order],
                "without_extra": {"months": base.months, "total_interest": interest(base)},
                "difference": {"months_sooner": base.months - p.months,
                               "interest_saved": _dollars(base.total_interest_cents - p.total_interest_cents)}
                if ends else None,
                # which debts are left after 600 months depends on where the rolled-over minimums go
                "never": [names[k] for k in p.never],
            }

        first = plans["avalanche"]       # what's left out doesn't depend on the strategy
        out = {
            "extra_monthly": _dollars(extra_monthly_cents), "start": month(start),
            "plans": [plan(plans[s], plans[f"baseline_{s}"]) for s in STRATEGIES if strategy in (None, s)],
            "excluded": [{"name": names[k], "reason": EXCLUDED_REASONS[r]} for k, r in first.excluded],
            "assumptions": PLAN_ASSUMPTIONS,
        }
        # nothing planned reads as "debt-free in 0 months" to a model, so it gets no plan and says why
        if not first.order and not first.never:
            out["plans"] = []
            out["note"] = ("None of the debts could be planned; see excluded." if first.excluded
                           else "No debts with a balance on file.")
        return out

    def _goals(self, facts: GoalFacts, debts: Optional[Sequence[Debt]] = None) -> dict:
        data = {
            "monthly_surplus": _dollars(facts.surplus_cents), "cap": _dollars(facts.cap_cents),
            "offered_kinds": list(facts.offered), "emergency_fund_gap": _dollars(facts.gap_cents),
            "protected_categories": list(facts.protected_categories),
            # free text can name anyone
            "note": scrub_note(facts.note), "assumptions": GOAL_ASSUMPTIONS, "debts": [],
        }
        if facts.committed_cents:
            data["already_committed"] = _dollars(facts.committed_cents)
        if "debt_payoff" not in facts.offered:
            return data
        debts = load_debts(self._session, self._user_id) if debts is None else debts
        planned = sorted(planned_debts(debts, facts.start), key=lambda d: avalanche_key(d.apr, d.balance_cents))
        data["debts"] = [{"name": scrub_account_name(d.name), "kind": d.kind, "apr": d.apr,
                          "balance": _dollars(d.balance_cents), "min_payment": _dollars(d.min_payment_cents)}
                         for d in planned]

        def option(share: float) -> dict:
            extra = round(facts.cap_cents * share / 100) * 100       # to the dollar
            plans = compare(debts, extra, start=facts.start)
            return {"extra_monthly": _dollars(extra), **{
                s: {"months": plans[s].months,
                    "total_interest": _dollars(plans[s].total_interest_cents) if plans[s].months is not None else None}
                for s in STRATEGIES}}

        data["debt_plan_options"] = [option(share) for share in GOAL_PLAN_SHARES]
        return data

    # minimized per question: totals by category, plus one category's rows only when asked for
    def _spending(self, start: Optional[date], end: Optional[date], category: Optional[str]) -> dict:
        end = end or self.today()
        start = start or end - timedelta(days=DEFAULT_SPENDING_DAYS - 1)
        if start > end:
            raise ValueError("start date must be on or before end date")
        category = category.strip().upper() if category else None
        inflow = category in ("INCOME", "TRANSFER_IN")
        txns = self._session.scalars(
            select(Transaction)
            .where(Transaction.user_id == self._user_id, Transaction.removed.is_(False),
                   Transaction.amount_cents < 0 if inflow else Transaction.amount_cents > 0,
                   Transaction.date >= start, Transaction.date <= end)
            .order_by(Transaction.date.desc(), Transaction.id.desc())
        ).all()

        # Aggregate spending excludes money moved between accounts and repayments of card purchases.
        # Explicit income/transfer questions are supported separately, with their direction stated.
        txns = [t for t in txns if t.pf_category_detailed != CARD_PAYMENT and (
            t.pf_category_primary not in EXCLUDED_PRIMARY or t.pf_category_primary == category)]

        totals: dict[str, int] = {}
        for t in txns:
            key = t.pf_category_primary or "UNCATEGORIZED"
            totals[key] = totals.get(key, 0) + (-t.amount_cents if inflow else t.amount_cents)

        if category is None:
            return {k: _dollars(v) for k, v in sorted(totals.items(), key=lambda kv: -kv[1])}
        # plaid categories are upper-case enums; models often send them lower-case
        matching = [t for t in txns if (t.pf_category_primary or "UNCATEGORIZED") == category]
        rows = matching[:TXN_LIMIT]
        return {
            "category": category,
            "total": _dollars(totals.get(category, 0)),   # an explicit $0, not "no data"
            "direction": "inflow" if inflow else "outflow",
            "count": len(matching), "truncated": len(matching) > TXN_LIMIT,
            "transactions": [{"date": t.date, "amount": _dollars(-t.amount_cents if inflow else t.amount_cents),
                              "merchant": None if inflow else scrub_merchant(t.merchant_name)} for t in rows],
        }

    # one flagged category only (architecture section 7: a dining diagnosis sees dining). built from the facts
    # the budget engine computed, field by field; merchants scrubbed, money in dollars
    def _diagnose(self, facts: OverspendFacts, transactions: list[Txn]) -> dict:
        # the same spending filter as the facts: a card payment left out of "spent" isn't quoted as evidence
        rows = sorted((t for t in spending(transactions) if t.category == facts.category), key=lambda t: t.date,
                      reverse=True)[:TXN_LIMIT_DIAGNOSE]
        n = len(facts.monthly_totals)
        largest = facts.largest
        return {
            "category": facts.category, "reason": facts.reason, "kind": facts.kind,
            "month": _month_name(facts.month), "limit": _dollars(facts.limit_cents),
            "spent": _dollars(facts.spent_cents),
            "pace": None if facts.pace is None else round(facts.pace, 2),
            "day": facts.days_elapsed, "days_in_month": facts.days_in_period,
            "projected": _dollars(facts.projected_cents),
            "largest": None if largest is None else {
                "date": largest.date, "amount": _dollars(largest.amount_cents),
                "merchant": scrub_merchant(largest.merchant), "share_of_spent": round(facts.largest_share, 2),
                "times_typical": None if facts.largest_vs_typical is None else round(facts.largest_vs_typical, 1)},
            "merchants": [{"merchant": scrub_merchant(m.merchant), "this_month": _dollars(m.period_cents),
                           "charges": m.charges, "months_seen_before": m.prior_months, "pattern": m.pattern,
                           "typical": _dollars(m.typical_cents)} for m in facts.merchants],
            "history": [{"month": _month_name(add_months(facts.month, i - n)), "total": _dollars(total)}
                        for i, total in enumerate(facts.monthly_totals)],
            "outlier_months": [_month_name(m) for m in facts.outlier_months],
            "transactions": [{"date": t.date, "amount": _dollars(t.amount_cents),
                              "merchant": scrub_merchant(t.merchant)} for t in rows],
        }

    # append-only record of exactly what left the system; pseudonym only, never the user id
    def _audit(self, purpose: str, params: dict, prompt: str) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pseudonym": self.pseudonym, "purpose": purpose, "params": params, "prompt": prompt,
        }
        entry = (json.dumps(line, default=str, ensure_ascii=False) + "\n").encode("utf-8")
        if len(entry) > config.PROMPT_AUDIT_MAX_BYTES:
            raise ValueError("prompt exceeds the configured audit log size limit")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        with _audit_lock:
            fd = os.open(self._audit_path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                if os.fstat(fd).st_size + len(entry) > config.PROMPT_AUDIT_MAX_BYTES:
                    os.close(fd)
                    fd = None
                    self._audit_path.replace(self._audit_path.with_name(self._audit_path.name + ".1"))
                    fd = os.open(self._audit_path, flags, 0o600)
                with os.fdopen(fd, "ab") as f:
                    fd = None
                    f.write(entry)
            finally:
                if fd is not None:
                    os.close(fd)
