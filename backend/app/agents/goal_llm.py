from typing import Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.agents.budget_llm import Text

# the goal graph's llm call (spec: tpc-23): the facts come from the privacy gateway as a json string, and the model
# picks which goals, their order and their monthly amounts. code computes each goal's target and date

GOAL_PROMPT = (
    "You propose savings and debt goals for a personal finance app. The facts below were computed from the user's "
    "accounts and transactions. Propose 1 to 3 goals, most important first, at most one of each kind, using only the "
    "kinds in offered_kinds.\n"
    "If any debt has an APR of 8% or more, debt_payoff comes first if it's offered, with the avalanche strategy "
    "unless the user's note asks otherwise.\n"
    "Set monthly_cents for each goal, in cents: $200.00 is 20000. Together they must not exceed cap, which is in "
    "dollars. For debt_payoff, monthly_cents is an extra on top of the minimum payments, and strategy is avalanche "
    "or snowball; debt_plan_options shows what some extras would do. Leave strategy empty for the other kinds.\n"
    "Respect the user's note. Don't propose anything that touches a category in protected_categories.\n"
    "Write rationale to the user in one or two plain sentences, using only numbers in the facts. Don't give "
    "financial advice beyond the plan. Answer in JSON with goals."
)


class ProposedGoal(BaseModel):
    kind: Literal["debt_payoff", "emergency_fund", "savings"]
    monthly_cents: int = Field(gt=0)
    strategy: Optional[Literal["avalanche", "snowball"]] = None
    rationale: Text


class GoalProposal(BaseModel):
    goals: list[ProposedGoal] = Field(min_length=1, max_length=3)    # in priority order


def goal_messages(context: str) -> list:
    return [SystemMessage(GOAL_PROMPT), HumanMessage(f"Facts:\n{context}")]
