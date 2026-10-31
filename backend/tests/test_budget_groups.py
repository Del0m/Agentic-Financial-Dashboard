from app.budget_engine.groups import default_group, default_name


def test_default_groups():
    assert default_group("FOOD_AND_DRINK") == "needs"
    assert default_group("RENT_AND_UTILITIES") == "needs"
    assert default_group("ENTERTAINMENT") == "wants"
    assert default_group("SAVINGS_TRANSFER") == "saving"
    assert default_group("FREE") == "free"


def test_default_names():
    assert default_name("FOOD_AND_DRINK") == "Food and drink"
    assert default_name("RENT_AND_UTILITIES") == "Rent and utilities"
    assert default_name("ENTERTAINMENT") == "Entertainment"
    assert default_name("SAVINGS_TRANSFER") == "Savings"
    assert default_name("FREE") == "Free"
