"""Score the recurring detector against the streams planted in the synthetic users.

A stream is planted when a Monthly or Every pattern has 3 or more charges in the 180 days before the
user's run date. Weekly habits, Once purchases and Scatter noise are everyday spending, so a stream
found for one of them is a false positive. Also measures the monthly next date over a sweep of bill
days. Results go to eval/results/<date>/recurring_accuracy/results.json.

Run from the repo root:
    venv/bin/python eval/recurring_accuracy.py
"""
import calendar
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# make `eval` and `app` importable however the script is launched (same trick as run_eval.py)
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from app.budget_engine.recurring import RTxn, detect, merchant_key
from app.budget_engine.txns import add_months
from eval.budget_users import (GOAL_USERS, PLANTED, PROFILES, RECURRING_USERS, Every, Monthly, Profile, detailed_for,
                               transactions)
from eval.run_eval import RESULTS_DIR, _git_commit, _write, results_dir

WINDOW_DAYS = 180


def as_of(profile: Profile) -> date:
    return profile.run_date or profile.today


# load_rtxns doesn't exist yet (the stored streams bring it), so the categories are as planted
def rtxns(profile: Profile, day: date) -> list[RTxn]:
    return [RTxn(d, cents, merchant, category, detailed_for(category, merchant))
            for d, cents, category, merchant in transactions(profile) if d <= day]


# (direction, merchant key, frequency) of each planted stream with 3 or more charges in the window
def planted_streams(profile: Profile, day: date) -> set[tuple[str, str, str]]:
    since = day - timedelta(days=WINDOW_DAYS)
    planted = set()
    for p in profile.patterns:
        if isinstance(p, Monthly):
            frequency = "monthly"
        elif isinstance(p, Every):
            frequency = {7: "weekly", 14: "biweekly"}[p.days]
        else:
            continue
        one = Profile(profile.id, profile.description, profile.seed, (p,), start=profile.start, end=profile.end)
        if sum(since <= d <= day for d, *_ in transactions(one)) >= 3:
            planted.add(("in" if p.cents < 0 else "out", merchant_key(p.merchant), frequency))
    return planted


def _ratio(hits: int, total: int) -> float:
    return hits / total if total else 1.0


def score(profile: Profile) -> dict:
    day = as_of(profile)
    planted = planted_streams(profile, day)
    detected = {(s.direction, s.merchant_key, s.frequency) for s in detect(rtxns(profile, day), day)}
    return {"id": profile.id, "as_of": day.isoformat(), "planted": sorted(planted), "detected": sorted(detected),
            "precision": _ratio(len(detected & planted), len(detected)),
            "recall": _ratio(len(detected & planted), len(planted)),
            "false_positives": sorted(detected - planted), "misses": sorted(planted - detected)}


def _purchase_days(txns: list[RTxn], removed: set[tuple[str, str]], day: date) -> int:
    since = day - timedelta(days=90)
    return len({t.date for t in txns if t.date > since and t.amount_cents > 0
                and ("out", merchant_key(t.merchant)) not in removed})


# days with an everyday purchase once detected streams are removed, as a share of the days left when only the planted
# streams are: what the forecast's everyday estimate would lose to false streams
def everyday_share(profile: Profile) -> float:
    day = as_of(profile)
    txns = rtxns(profile, day)
    detected = {(s.direction, s.merchant_key) for s in detect(txns, day)}
    oracle = {(direction, key) for direction, key, _ in planted_streams(profile, day)}
    return _purchase_days(txns, detected, day) / _purchase_days(txns, oracle, day)


def _business_day(d: date) -> date:
    return d + timedelta(days={5: 2, 6: 1}.get(d.weekday(), 0))


def _paid(bill_day: int, month: date) -> date:
    return _business_day(month.replace(day=min(bill_day, calendar.monthrange(month.year, month.month)[1])))


# a bill on each day 1 to 31, paid the next business day, 3 charges, 12 start months: how far off is the next date?
def next_date_errors(year: int = 2026) -> list[int]:
    errors = []
    for bill_day in range(1, 32):
        for start in range(1, 13):
            first = date(year, start, 1)
            paid = [_paid(bill_day, add_months(first, i)) for i in range(4)]
            streams = detect([RTxn(d, 1599, "Bill", "GENERAL_SERVICES", None) for d in paid[:3]], paid[2])
            errors.append(abs((streams[0].next_date - paid[3]).days) if streams else 999)
    return errors


def next_date_summary(errors: list[int]) -> dict:
    return {"cases": len(errors), "within_1_day": sum(e <= 1 for e in errors), "max_error": max(errors),
            "by_error": {str(e): errors.count(e) for e in sorted(set(errors))}}


def _print_table(results: list[dict], pooled: dict, next_date: dict) -> None:
    print(f"{'profile':<26} {'precision':>9} {'recall':>7}  false positives / misses")
    for r in results:
        print(f"{r['id']:<26} {r['precision']:>9.2f} {r['recall']:>7.2f}  "
              f"{[k for _, k, _ in r['false_positives']]} / {[k for _, k, _ in r['misses']]}")
    print(f"{'pooled':<26} {pooled['precision']:>9.2f} {pooled['recall']:>7.2f}")
    print(f"noisy everyday share: {pooled['noisy_everyday_share']}")
    print(f"monthly next date: {next_date}")


def run(profiles, out_root: Path, day: date) -> Path:
    results = [score(p) for p in profiles]
    found = sum(len(r["detected"]) for r in results)
    planted = sum(len(r["planted"]) for r in results)
    hits = found - sum(len(r["false_positives"]) for r in results)
    noisy = next((p for p in profiles if p.id == "recurring-noisy"), None)
    pooled = {"precision": _ratio(hits, found), "recall": _ratio(hits, planted),
              "noisy_everyday_share": round(everyday_share(noisy), 3) if noisy else None}
    next_date = next_date_summary(next_date_errors())
    _print_table(results, pooled, next_date)
    path = results_dir(out_root, day, "recurring_accuracy") / "results.json"
    _write(path, {"git_commit": _git_commit(), "profiles": results, "pooled": pooled, "next_date": next_date})
    print(f"\nwrote {path}")
    return path


def main(argv=None) -> int:
    run((*PROFILES, *PLANTED, *GOAL_USERS, *RECURRING_USERS), RESULTS_DIR, date.today())
    return 0


if __name__ == "__main__":
    sys.exit(main())
