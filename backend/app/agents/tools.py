import json
import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from ..budget_engine.txns import round_cents
from ..privacy import gateway as gw
from ..privacy.gateway import PrivacyGateway
from ..rag.store import KnowledgeBase

logger = logging.getLogger(__name__)

# the chat agent's tools (architecture section 9, M2). every user-data tool reads through the privacy
# gateway, so allowlists, scrubbing, the pseudonym and the audit log apply to all of them.
# schemas are kept small and plain for an 8B model: few args, strings, descriptions that say when.

# plaid's personal_finance_category primary values, which are our budget categories (decision log)
PFC_CATEGORIES = (
    "INCOME", "TRANSFER_IN", "TRANSFER_OUT", "LOAN_PAYMENTS", "BANK_FEES", "ENTERTAINMENT",
    "FOOD_AND_DRINK", "GENERAL_MERCHANDISE", "HOME_IMPROVEMENT", "MEDICAL", "PERSONAL_CARE",
    "GENERAL_SERVICES", "GOVERNMENT_AND_NON_PROFIT", "TRANSPORTATION", "TRAVEL", "RENT_AND_UTILITIES",
)
# the gateway's label for transactions plaid left uncategorized
VALID_CATEGORIES = PFC_CATEGORIES + ("UNCATEGORIZED",)

SEARCH_K = 4
MAX_QUERY_CHARS = 1000
MAX_EXTRA_DOLLARS = 1_000_000


# small models send "" for an optional argument they mean to leave out
def _blank(value: Optional[str]) -> Optional[str]:
    return value.strip() or None if value is not None else None


def _parse_date(name: str, value: Optional[str]) -> Optional[date]:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ToolException(f"{name} must be YYYY-MM-DD, got '{value}'")


# tools are closures over one gateway, which already holds the user: the model never sees or picks a user id
def make_tools(gateway: PrivacyGateway, kb: KnowledgeBase) -> list[BaseTool]:
    def get_accounts() -> str:
        return gateway.build_context("accounts")

    def get_debts() -> str:
        return gateway.build_context("debts")

    def get_recurring() -> str:
        return gateway.build_context("recurring")

    def get_cash_forecast() -> str:
        return gateway.build_context("cash_flow")

    def get_spending(category: Optional[str] = None, start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> str:
        start, end = _parse_date("start_date", _blank(start_date)), _parse_date("end_date", _blank(end_date))
        # the gateway fills a missing end with today, so a future start alone is reversed too
        if start and start > (end or gateway.today()):
            raise ToolException("start_date must be on or before end_date (today when end_date is left out)")
        category = _blank(category)
        if category is not None:
            # an unknown word would come back as $0 spent, which reads as a real answer;
            # echo what the model sent so it recognizes its own mistake
            if category.upper() not in VALID_CATEGORIES:
                raise ToolException(f"unknown category '{category}'; use one of: {', '.join(VALID_CATEGORIES)}")
            category = category.upper()
        return gateway.build_context("spending", start=start, end=end, category=category)

    # skips the gateway: results are public guidance with no user data, and the query only
    # goes to the local embedding model (architecture section 7)
    def search_kb(query: str) -> str:
        query = query.strip()[:MAX_QUERY_CHARS]
        if not query:
            raise ToolException("query must not be empty")
        if kb.count() == 0:
            logger.warning("knowledge base is empty; run scripts/ingest_kb.py")
            raise ToolException("knowledge base is empty")
        return json.dumps({"purpose": "kb", "results": kb.search(query, k=SEARCH_K)},
                          separators=(",", ":"), ensure_ascii=False)

    # the planner's numbers come from code (tpc-32); the model only picks the extra and the method
    def plan_debt_payoff(extra_monthly: float = 0, strategy: Optional[str] = None) -> str:
        if not 0 <= extra_monthly <= MAX_EXTRA_DOLLARS:
            raise ToolException(f"extra_monthly must be between 0 and {MAX_EXTRA_DOLLARS:,} dollars, "
                                f"got {extra_monthly}")
        strategy = _blank(strategy)
        if strategy is not None:
            if strategy.lower() not in gw.STRATEGIES:
                raise ToolException(f"unknown strategy '{strategy}'; use avalanche or snowball, or leave it out")
            strategy = strategy.lower()
        return gateway.build_context("debt_plan", extra_monthly_cents=round_cents(Decimal(str(extra_monthly)) * 100),
                                     strategy=strategy)

    return [
        StructuredTool.from_function(
            get_accounts, name="get_accounts",
            description="List the user's bank accounts with type and balance in dollars. "
                        "Use for questions about balances, savings or how much money they have.",
        ),
        StructuredTool.from_function(
            get_debts, name="get_debts",
            description="List the user's debts (credit cards and loans) with APR, balance and minimum payment, "
                        "highest APR first. Use for questions about debt, interest or what to pay off first.",
        ),
        StructuredTool.from_function(
            get_spending, name="get_spending",
            description="Total spending in dollars by category. Optional: category (one of "
                        f"{', '.join(VALID_CATEGORIES)}) to also list that category's transactions "
                        "(INCOME and TRANSFER_IN report money in); "
                        "start_date and end_date as YYYY-MM-DD (default: the last 30 days).",
        ),
        StructuredTool.from_function(
            search_kb, name="search_kb",
            description="Search trusted public financial guidance (CFPB, IRS, SEC investor.gov, FDIC) for general "
                        "rules and strategies: retirement accounts, debt payoff methods, budgeting rules, savings "
                        "and deposit insurance. Returns titled passages; refer to a passage by its title, never by "
                        "a link. Not for the user's own balances or spending.",
        ),
        StructuredTool.from_function(
            plan_debt_payoff, name="plan_debt_payoff",
            description="Simulate paying off the user's debts month by month (avalanche: highest APR first; "
                        "snowball: smallest balance first), optionally with an extra amount per month. Returns "
                        "months to debt-free, total interest, payoff dates and what the extra saves. Use for how "
                        "long until debt-free, what paying more changes, or avalanche vs snowball. Quote its "
                        "numbers exactly: give the months to debt-free and the total interest, then what the "
                        "extra saves.",
        ),
        StructuredTool.from_function(
            get_recurring, name="get_recurring",
            description="List the user's recurring charges, subscriptions and paycheck patterns. "
                        "Use for what subscriptions they have or how much they pay monthly. Amounts are in dollars.",
        ),
        StructuredTool.from_function(
            get_cash_forecast, name="get_cash_forecast",
            description="Forecast checking cash as a range and identify upcoming low-balance dips. "
                        "Use for will I run short before payday. State a range and say when no income is assumed.",
        ),
    ]
