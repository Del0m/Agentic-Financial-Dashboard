from app.budget_engine.txns import FREE, GOAL_DEBT_PAYOFF, GOAL_EMERGENCY_FUND, GOAL_SAVINGS, SAVINGS

# which group a new line starts in, and what it's called, until the user changes either (tpc-22).
# plaid's FOOD_AND_DRINK mixes groceries and dining; it starts in needs and the user can move it

NEEDS_DEFAULT = frozenset({"RENT_AND_UTILITIES", "LOAN_PAYMENTS", "TRANSPORTATION", "MEDICAL", "FOOD_AND_DRINK"})
_RESERVED = {SAVINGS: ("Savings", "saving"), FREE: ("Free", "free"),
             GOAL_DEBT_PAYOFF: ("Debt payoff", "goal"), GOAL_EMERGENCY_FUND: ("Emergency fund", "goal"),
             GOAL_SAVINGS: ("Savings goal", "goal")}


def default_group(category: str) -> str:
    if category in _RESERVED:
        return _RESERVED[category][1]
    return "needs" if category in NEEDS_DEFAULT else "wants"


def default_name(category: str) -> str:
    if category in _RESERVED:
        return _RESERVED[category][0]
    words = category.split("_")
    return " ".join([words[0].capitalize()] + [w.lower() for w in words[1:]])
