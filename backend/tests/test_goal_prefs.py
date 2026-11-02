import pytest

from app.budget_service.goal_prefs import GoalPrefs, set_goal_prefs, user_goal_prefs
from app.db.models import User
from tests.factories import add_user

EXAMPLE = {"skip_kinds": ["savings"], "max_share": 0.5, "protected_categories": ["PERSONAL_CARE"],
           "note": "emergency fund matters most"}


@pytest.fixture
def user(db):
    with db() as s:
        add_user(s, "u1", "u1@x")
        s.commit()
    return db


def test_defaults_with_nothing_stored(user):
    with user() as s:
        assert user_goal_prefs(s, "u1") == GoalPrefs((), 0.8, (), None)


def test_round_trip(user):
    with user() as s:
        out = set_goal_prefs(s, "u1", EXAMPLE)
        s.commit()
    with user() as s:
        assert user_goal_prefs(s, "u1") == out == GoalPrefs(("savings",), 0.5, ("PERSONAL_CARE",),
                                                            "emergency fund matters most")


def test_other_prefs_survive(user):
    with user() as s:
        s.get(User, "u1").prefs_json = {"budget": {"style": "caps"}, "sim": {"date": "2026-10-01"}}
        s.commit()
        set_goal_prefs(s, "u1", {"max_share": 0.5})
        s.commit()
    with user() as s:
        prefs = s.get(User, "u1").prefs_json
        assert prefs["budget"] == {"style": "caps"} and prefs["sim"] == {"date": "2026-10-01"}
        assert prefs["goals"]["max_share"] == 0.5


@pytest.mark.parametrize("raw", [{"skip_kinds": ["yacht"]}, {"max_share": 0}, {"max_share": 1.5},
                                 {"max_share": "lots"}, {"protected_categories": ["NOPE"]},
                                 {"note": "x" * 501}, {"max_shar": 0.5}])
def test_invalid_raises(user, raw):
    with user() as s:
        with pytest.raises(ValueError):
            set_goal_prefs(s, "u1", raw)
        s.commit()
    with user() as s:
        assert user_goal_prefs(s, "u1") == GoalPrefs()
