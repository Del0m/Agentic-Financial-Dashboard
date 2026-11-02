from datetime import date as Date, datetime, timezone
from typing import Literal, Optional
from sqlalchemy import JSON, Double, Enum, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base

# Money is always integer cents (Mapped[int]), never float.
# Sign convention: amount_cents follows Plaid, positive = money leaving the account
# (see docs/architecture.md section 5).

# get current time
def utcnow() -> datetime:
    return datetime.now(timezone.utc)

# make user table, with id, email, pass, created, and plaid items
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)      # uuid as string
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    prefs_json: Mapped[dict] = mapped_column(JSON, default=dict)

    items: Mapped[list["PlaidItem"]] = relationship(back_populates="user")

# id of item, user owning it, and access token to read in plaid
class PlaidItem(Base):
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plaid_item_id: Mapped[str] = mapped_column(unique=True)
    access_token_enc: Mapped[str]                           # Fernet-encrypted, never plaintext
    institution_name: Mapped[Optional[str]]
    txn_cursor: Mapped[Optional[str]]                       # None = "not synced yet"
    status: Mapped[str] = mapped_column(default="active")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped["User"] = relationship(back_populates="items")
    accounts: Mapped[list["Account"]] = relationship(back_populates="item")

# a bank account under a plaid item; balances can be missing from plaid so they're nullable
class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"), index=True)
    plaid_account_id: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    mask: Mapped[Optional[str]]
    type: Mapped[str]
    subtype: Mapped[Optional[str]]
    current_balance_cents: Mapped[Optional[int]]
    available_balance_cents: Mapped[Optional[int]]
    limit_cents: Mapped[Optional[int]]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    item: Mapped["PlaidItem"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")

# debt details for an account (credit card, student loan, mortgage)
# one row per account, so account_id is unique and refreshing liabilities is an upsert
class Liability(Base):
    __tablename__ = "liabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, index=True)
    kind: Mapped[str] = mapped_column(Enum("credit", "student", "mortgage", name="liability_kind"))
    # a percentage, not money, so float is fine. Float pinned: sqlalchemy 2.1 maps float to Double, which
    # isn't what the column was created as
    apr: Mapped[Optional[float]] = mapped_column(Float)
    min_payment_cents: Mapped[Optional[int]]
    next_due_date: Mapped[Optional[Date]]
    balance_cents: Mapped[Optional[int]]

# one row per plaid transaction; plaid_txn_id is unique so re-syncing is an upsert
class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    plaid_txn_id: Mapped[str] = mapped_column(unique=True)
    date: Mapped[Date] = mapped_column(index=True)
    amount_cents: Mapped[int]
    merchant_name: Mapped[Optional[str]]
    pf_category_primary: Mapped[Optional[str]]
    pf_category_detailed: Mapped[Optional[str]]
    our_category: Mapped[Optional[str]]
    user_category: Mapped[Optional[str]]                    # the user's correction; wins over every other source
    is_recurring: Mapped[bool] = mapped_column(default=False)
    pending: Mapped[bool] = mapped_column(default=False)
    removed: Mapped[bool] = mapped_column(default=False)

    account: Mapped["Account"] = relationship(back_populates="transactions")

# one budget per user per calendar month (architecture 6.4); fit_json keeps the months fitted and the
# categories skipped so the agent can explain the budget. null for a period opened by period close
class BudgetPeriod(Base):
    __tablename__ = "budget_periods"
    __table_args__ = (UniqueConstraint("user_id", "start_date", name="uq_budget_periods_user_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    start_date: Mapped[Date]
    end_date: Mapped[Date]
    status: Mapped[str] = mapped_column(Enum("open", "closed", name="budget_period_status"), default="open")
    fit_json: Mapped[Optional[dict]] = mapped_column(JSON)
    income_cents: Mapped[Optional[int]]                     # typical monthly income; null with no income history
    total_cents: Mapped[Optional[int]]                      # the planned total; the style decides how it's built
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    lines: Mapped[list["BudgetLine"]] = relationship(back_populates="period")

# budget lines belong to a group, which is what the budget rules read (tpc-22); the name is the user's label.
# plain strings, like the alert columns, so a new group or style never needs a migration
LineGroup = Literal["needs", "wants", "saving", "free"]
BudgetStyle = Literal["caps", "50_30_20"]

# one limit per category per period; fit_json holds the monthly totals and outlier months behind the limit.
# category is the internal key for what the line tracks (a plaid category, SAVINGS_TRANSFER or FREE)
class BudgetLine(Base):
    __tablename__ = "budget_lines"
    __table_args__ = (UniqueConstraint("period_id", "category", name="uq_budget_lines_period_category"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("budget_periods.id"), index=True)
    category: Mapped[str]
    name: Mapped[str]
    group: Mapped[str]
    kind: Mapped[str] = mapped_column(Enum("fixed", "flexible", name="budget_line_kind"))
    limit_cents: Mapped[int]
    spent_cents: Mapped[int] = mapped_column(default=0)
    # a ratio, not money. Double pinned to match the migration: a bare float is Float on sqlalchemy 2.0
    last_pace: Mapped[Optional[float]] = mapped_column(Double)
    source: Mapped[str] = mapped_column(Enum("init", "adapted", "user", name="budget_line_source"))
    fit_json: Mapped[Optional[dict]] = mapped_column(JSON)

    period: Mapped["BudgetPeriod"] = relationship(back_populates="lines")

# a merchant the user always wants in one category ("always do this for costco"). merchant_name is stored
# trimmed and casefolded so "Costco" and "COSTCO " are one rule
class CategoryRule(Base):
    __tablename__ = "category_rules"
    __table_args__ = (UniqueConstraint("user_id", "merchant_name", name="uq_category_rules_user_merchant"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    merchant_name: Mapped[str]
    category: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

# alert and advice columns are plain strings so a new kind or action never needs a migration;
# these list the values architecture section 5 allows
AlertKind = Literal["overspend", "new_subscription", "price_increase", "income_change", "low_balance",
                    "extra_payment"]
AlertCause = Literal["one_time", "seasonal", "recurring"]
AlertAction = Literal["alert", "suggest_cuts", "adjust_next_budget", "suggest_cancel", "move_from_savings",
                      "delay_spend", "extra_payment"]
AlertStatus = Literal["pending_user", "accepted", "dismissed"]
AlertSeverity = Literal["medium", "high"]            # pace flag, over its limit
AdviceOutcome = Literal["accepted", "dismissed", "ignored"]

# something the agent wants the user to see or approve; stream_id arrives with recurring_streams (TPC-33)
class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    period_id: Mapped[Optional[int]] = mapped_column(ForeignKey("budget_periods.id"), index=True)
    kind: Mapped[str]
    category: Mapped[Optional[str]]
    severity: Mapped[str]
    cause: Mapped[Optional[str]]
    action: Mapped[Optional[str]]
    proposed_limit_cents: Mapped[Optional[int]]            # set by code for adjust_next_budget; close applies it
    message: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending_user")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

# what the agent told the user, read back so it doesn't repeat or contradict itself
class AdviceLog(Base):
    __tablename__ = "advice_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    # the alert this advice became, so the user's accept/dismiss reaches it as the outcome
    alert_id: Mapped[Optional[int]] = mapped_column(ForeignKey("alerts.id", name="fk_advice_log_alert_id_alerts"),
                                                    index=True)
    category: Mapped[Optional[str]]
    advice_text: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    outcome: Mapped[Optional[str]]

# one goal the agent proposed (tpc-23). code computes the target and the date; the llm chose the kind, order and amount.
# proposed -> superseded by the next run, or accepted | dismissed once the user can decide (tpc-25)
class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("agent_runs.id"))
    kind: Mapped[str]
    priority: Mapped[int]
    monthly_cents: Mapped[int]
    target_cents: Mapped[int]
    target_date: Mapped[Optional[Date]]      # none only for a debt plan that never ends
    strategy: Mapped[Optional[str]]          # debt_payoff only
    rationale: Mapped[str]
    status: Mapped[str] = mapped_column(default="proposed")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


# one row per graph run (budget, close, goal)
class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    graph: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[Optional[datetime]]
    status: Mapped[str]
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
