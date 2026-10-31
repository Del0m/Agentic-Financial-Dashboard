from app.budget_engine.pace import BudgetLine
from app.budget_engine.styles import DEFAULT_TARGETS, StyleFact, apply_style

RENT = BudgetLine("RENT_AND_UTILITIES", "fixed", 150000, "needs")
FOOD = BudgetLine("FOOD_AND_DRINK", "flexible", 60000, "needs")
FUN = BudgetLine("ENTERTAINMENT", "flexible", 20000, "wants")
SAVING = BudgetLine("SAVINGS_TRANSFER", "fixed", 50000, "saving")


def caps(lines, income, fallback=0):
    return apply_style("caps", lines, income, fallback, DEFAULT_TARGETS)


def split(lines, income, targets=DEFAULT_TARGETS, fallback=0):
    return apply_style("50_30_20", lines, income, fallback, targets)


def test_caps_free_is_income_minus_lines():
    r = caps([RENT, FOOD, FUN, SAVING], 420000)
    assert r.limits == {"RENT_AND_UTILITIES": 150000, "FOOD_AND_DRINK": 60000, "ENTERTAINMENT": 20000,
                        "SAVINGS_TRANSFER": 50000, "FREE": 140000}
    assert (r.total_cents, r.facts, r.fallback) == (420000, (), None)


# free only exists when there's room in income; past that the total rises and the user is told, nothing is cut
def test_caps_free_bottoms_out_and_total_rises():
    big_rent = BudgetLine("RENT_AND_UTILITIES", "fixed", 320000, "needs")
    r = caps([big_rent, FOOD, FUN, SAVING], 420000)
    assert (r.limits["FREE"], r.total_cents) == (0, 450000)
    assert r.limits["RENT_AND_UTILITIES"] == 320000
    assert r.facts == (StyleFact("over_income", None, 450000, 420000, None),)


# no income history: the spending fallback is the total, and "more than your income" would be false
def test_caps_without_income_uses_fallback():
    r = caps([RENT, FOOD], None, fallback=230000)
    assert (r.limits["FREE"], r.total_cents, r.facts) == (20000, 230000, ())
    assert caps([RENT, FOOD], None, fallback=200000).facts == ()


def test_503020_targets():
    groceries = BudgetLine("FOOD_AND_DRINK", "flexible", 40000, "needs")     # needs $1,900 of a $2,000 target
    r = split([RENT, groceries, FUN, SAVING], 400000)
    assert r.limits["SAVINGS_TRANSFER"] == 80000          # 20% of income, not the fitted $500
    assert r.limits["FREE"] == 120000 - 20000              # the room left in wants
    assert r.limits["RENT_AND_UTILITIES"] == 150000 and r.limits["FOOD_AND_DRINK"] == 40000
    assert r.total_cents == 400000 and r.facts == () and r.fallback is None


def test_503020_creates_saving_line():
    assert split([RENT, FUN], 400000).limits["SAVINGS_TRANSFER"] == 80000


def test_503020_group_over_target():
    big_rent = BudgetLine("RENT_AND_UTILITIES", "fixed", 170000, "needs")
    r = split([big_rent, FOOD, FUN], 400000)
    assert r.facts == (StyleFact("group_over_target", "needs", 230000, 400000, 50),)
    assert r.limits["FREE"] == 100000


def test_503020_custom_targets():
    r = split([RENT, FOOD, FUN], 400000, targets={"needs": 60, "wants": 25, "saving": 15})
    assert (r.limits["SAVINGS_TRANSFER"], r.limits["FREE"], r.facts) == (60000, 80000, ())


def test_503020_without_income_falls_back():
    r = split([RENT, FOOD, FUN, SAVING], None, fallback=300000)
    plain = caps([RENT, FOOD, FUN, SAVING], None, fallback=300000)
    assert (r.limits, r.total_cents, r.facts) == (plain.limits, plain.total_cents, plain.facts)
    assert r.fallback == "no_income" and r.limits["SAVINGS_TRANSFER"] == 50000
