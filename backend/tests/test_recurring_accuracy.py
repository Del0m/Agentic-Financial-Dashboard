import json
from datetime import date

from eval.budget_users import GOAL_USERS, PLANTED, PROFILES, RECURRING_USERS
from eval.recurring_accuracy import everyday_share, next_date_errors, rtxns, run, score

GATE = (*PROFILES, *PLANTED, *GOAL_USERS, *RECURRING_USERS)


def recurring(pid):
    return next(p for p in RECURRING_USERS if p.id == pid)


def test_planted_users_are_found_exactly():
    r = score(recurring("recurring-planted"))
    assert (r["precision"], r["recall"]) == (1.0, 1.0), r
    keys = {key for _, key, _ in r["detected"]}
    assert {"streamly", "landlord", "employer", "fitclub gym", "clipflix", "newflix"} <= keys
    assert not keys & {"pottery studio", "grocer", "fuel"}


def test_the_noisy_profile_has_at_most_one_false_stream():
    r = score(recurring("recurring-noisy"))
    assert len(r["false_positives"]) <= 1, r
    assert r["recall"] == 1.0, r


def test_the_noisy_profile_keeps_its_everyday_spending():
    assert everyday_share(recurring("recurring-noisy")) >= 0.85


def test_every_profile_scores_at_least_0_95():
    scores = [score(p) for p in GATE]
    tp = sum(len(r["detected"]) - len(r["false_positives"]) for r in scores)
    assert tp / sum(len(r["detected"]) for r in scores) >= 0.95
    assert tp / sum(len(r["planted"]) for r in scores) >= 0.95


def test_weekly_habits_and_one_offs_are_never_streams():
    for p in GATE:
        keys = {key for _, key, _ in score(p)["detected"]}
        assert not keys & {"grocer", "fuel", "corner shop", "airline", "diner"}, p.id


def test_monthly_next_dates_are_close():
    errors = next_date_errors()
    assert len(errors) == 372
    assert sum(e <= 1 for e in errors) / 372 >= 0.75
    assert max(errors) <= 4


def test_rtxns_stop_at_the_as_of_date():
    p = recurring("recurring-planted")
    assert max(t.date for t in rtxns(p, date(2026, 10, 25))) <= date(2026, 10, 25)


def test_results_json_has_the_documented_keys(tmp_path):
    path = run([recurring("recurring-planted")], tmp_path, date(2026, 11, 2))
    data = json.loads(path.read_text())
    assert set(data) == {"git_commit", "profiles", "pooled", "next_date"}
    assert {"id", "precision", "recall", "false_positives", "misses"} <= set(data["profiles"][0])
    assert {"precision", "recall"} <= set(data["pooled"])
