"""Supported historical survey-year rules shared by import and batch loading."""

from datetime import datetime
from zoneinfo import ZoneInfo


MIN_SURVEY_YEAR = 2022
SURVEY_YEAR_INTERVAL = 2
PHILIPPINE_TIME = ZoneInfo("Asia/Manila")


def latest_survey_year():
    """Return the latest year an administrator may declare for an import."""
    current_year = datetime.now(PHILIPPINE_TIME).year
    elapsed_years = current_year - MIN_SURVEY_YEAR
    return current_year - (elapsed_years % SURVEY_YEAR_INTERVAL)


def supported_survey_years():
    """Return selectable survey years in two-year intervals from the pilot."""
    return tuple(range(
        MIN_SURVEY_YEAR,
        latest_survey_year() + 1,
        SURVEY_YEAR_INTERVAL,
    ))


def is_supported_survey_year(value):
    """Accept two-year survey cycles from the pilot through the current year."""
    return (
        type(value) is int
        and MIN_SURVEY_YEAR <= value <= latest_survey_year()
        and (value - MIN_SURVEY_YEAR) % SURVEY_YEAR_INTERVAL == 0
    )


def parse_survey_year(value):
    """Parse one four-digit form value without guessing from source responses."""
    if (not isinstance(value, str) or len(value) != 4
            or not value.isascii() or not value.isdecimal()):
        raise ValueError("invalid_survey_year")
    year = int(value)
    if not is_supported_survey_year(year):
        raise ValueError("invalid_survey_year")
    return year
