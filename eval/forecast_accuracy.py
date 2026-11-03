"""Replay checking forecasts against planted future transactions, offline."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))
from cryptography.fernet import Fernet
os.environ.setdefault("FERNET_KEY", Fernet.generate_key().decode())

from app.budget_service.forecast_data import forecast_accounts, start_balance
from app.budget_service.forecast_service import snapshot
from eval.budget_users import FORECAST_USERS, seed_profile, transactions
from eval.fixtures import temp_db
from eval.run_eval import RESULTS_DIR, _git_commit, _write, results_dir


def balance_on(profile, day: date) -> int:
    return profile.opening_cents - sum(c for d, c, _, _ in transactions(profile) if d <= day)


def _metrics(rows: list[dict]) -> dict:
    n = len(rows)
    mean = lambda key: sum(row[key] for row in rows) / n if n else 0
    return {"n": n, "mae_d7_cents": round(mean("error7")), "mae_d14_cents": round(mean("error14")),
            "coverage_d7": mean("covered7"), "coverage_d14": mean("covered14"),
            "mean_band_width_cents": round(mean("width")),
            "baseline_mae_d7_cents": round(mean("baseline7")), "baseline_mae_d14_cents": round(mean("baseline14"))}


def score_profile(profile) -> tuple[dict, list[dict]]:
    rows, lead, bill = [], None, None
    day, last = profile.start + timedelta(days=90), profile.end - timedelta(days=14)
    target = date(2026, 10, 13)
    while day <= last:
        with temp_db() as db, db() as s:
            uid = seed_profile(s, profile, until=day)
            result = snapshot(s, uid, day).result
            current = start_balance(forecast_accounts(s, uid))
        if result.status == "ok" and len(result.days) >= 14:
            row = {}
            for horizon in (7, 14):
                forecast_day = result.days[horizon - 1]
                actual = balance_on(profile, day + timedelta(days=horizon))
                row[f"error{horizon}"] = abs(forecast_day.expected_cents - actual)
                row[f"covered{horizon}"] = int(forecast_day.low_cents <= actual <= forecast_day.high_cents)
                row[f"baseline{horizon}"] = abs(current - actual)
            row["width"] = sum(result.days[h - 1].high_cents - result.days[h - 1].low_cents for h in (7, 14)) / 2
            rows.append(row)
        if lead is None and result.dip and result.dip.date == target:
            lead = (target - day).days
            bill = result.dip.cause.name if result.dip.cause else None
        day += timedelta(days=1)
    return {"id": profile.id, **_metrics(rows), "dip_lead_days": lead, "dip_bill": bill,
            "dip_pass": lead is not None and lead >= 3 and bill == "Landlord"}, rows


def run(profiles, out_root: Path, day: date) -> Path:
    results, pooled_rows = [], []
    for profile in profiles:
        result, rows = score_profile(profile)
        results.append(result)
        pooled_rows.extend(rows)
    pooled = _metrics(pooled_rows)
    pooled["dip_pass"] = bool(results) and all(r["dip_pass"] for r in results)
    print(f"{'profile':<16} {'n':>4} {'MAE d7':>10} {'MAE d14':>10} {'cover d7':>10} {'cover d14':>10} {'flat d14':>10}  dip")
    for row in [*results, {"id": "pooled", **pooled}]:
        print(f"{row['id']:<16} {row['n']:>4} ${row['mae_d7_cents']/100:>9.2f} ${row['mae_d14_cents']/100:>9.2f} "
              f"{row['coverage_d7']:>9.1%} {row['coverage_d14']:>9.1%} ${row['baseline_mae_d14_cents']/100:>9.2f}  {row['dip_pass']}")
    for row in results:
        print(f"{row['id']}: dip lead {row['dip_lead_days']} days, bill {row['dip_bill']}; mean band width ${row['mean_band_width_cents']/100:.2f}")
    path = results_dir(out_root, day, "forecast_accuracy") / "results.json"
    _write(path, {"git_commit": _git_commit(), "profiles": results, "pooled": pooled})
    print(f"wrote {path}")
    return path


def main(argv=None) -> int:
    run(FORECAST_USERS, RESULTS_DIR, date.today())
    return 0


if __name__ == "__main__":
    sys.exit(main())
