from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.budget_engine.forecast import ForecastInput, ForecastResult, forecast
from app.budget_service.forecast_data import (everyday_series, forecast_accounts, history_days, inflow_count,
                                              load_card_obligations, start_balance)
from app.budget_service.forecast_prefs import user_buffer
from app.budget_service.goal_lines import committed_goals
from app.budget_service.recurring import current_streams


@dataclass(frozen=True)
class ForecastSnapshot:
    result: ForecastResult
    buffer_cents: int
    history_days: int


def snapshot(session: Session, user_id: str, today: date) -> ForecastSnapshot:
    accounts = forecast_accounts(session, user_id)
    days, buffer = history_days(session, user_id, today), user_buffer(session, user_id)
    result = forecast(ForecastInput(today, start_balance(accounts), current_streams(session, user_id, today),
        load_card_obligations(session, user_id), committed_goals(session, user_id),
        everyday_series(session, user_id, today, accounts), days, inflow_count(session, user_id, today), buffer))
    return ForecastSnapshot(result, buffer, days)
