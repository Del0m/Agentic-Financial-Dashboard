import json
import math
from datetime import date

import pytest

from app.budget_service.forecast_data import forecast_accounts, start_balance
from eval.budget_users import FORECAST_USERS, PROFILES, seed_profile
from eval.forecast_accuracy import balance_on, run


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    path=run(FORECAST_USERS,tmp_path_factory.mktemp('forecast-eval'),date(2026,11,2))
    return path,json.loads(path.read_text())


def test_the_planted_dip_is_found_at_least_3_days_ahead_and_names_rent(results):
    r=results[1]['profiles'][0]
    assert r['dip_pass'] and r['dip_lead_days']>=3 and r['dip_bill']=='Landlord'


def test_balance_on_matches_the_seeded_account(db):
    with db() as s:
        uid=seed_profile(s,FORECAST_USERS[0],until=date(2026,10,10))
        assert start_balance(forecast_accounts(s,uid))==balance_on(FORECAST_USERS[0],date(2026,10,10))


def test_mae_is_finite_and_coverage_is_a_share(results):
    for r in [*results[1]['profiles'],results[1]['pooled']]:
        assert math.isfinite(r['mae_d7_cents']) and math.isfinite(r['mae_d14_cents'])
        assert 0<=r['coverage_d7']<=1 and 0<=r['coverage_d14']<=1


def test_the_forecast_beats_a_flat_balance_at_d14_on_the_dip_user(results):
    r=results[1]['profiles'][0]
    assert r['mae_d14_cents']<=r['baseline_mae_d14_cents']


def test_results_json_has_the_documented_keys(results):
    assert {'n','mae_d7_cents','mae_d14_cents','coverage_d7','coverage_d14','mean_band_width_cents',
            'baseline_mae_d7_cents','baseline_mae_d14_cents','dip_lead_days','dip_bill','dip_pass'} <= set(results[1]['profiles'][0])


def test_only_forecast_users_are_scored(results):
    assert [r['id'] for r in results[1]['profiles']]==[p.id for p in FORECAST_USERS]


def test_existing_budget_accuracy_numbers_are_unchanged(tmp_path):
    from eval.budget_accuracy import run as budget_run
    r=json.loads(budget_run(PROFILES,tmp_path,date(2026,11,2)).read_text())
    assert r['pooled']=={'median':7.1,'mean':17.7}
