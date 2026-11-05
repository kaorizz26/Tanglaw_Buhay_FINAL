"""Supported historical survey-year rules shared by import and batch loading."""

from datetime import datetime
from zoneinfo import ZoneInfo


MIN_SURVEY_YEAR = 2022
PHILIPPINE_TIME = ZoneInfo("Asia/Manila")


def latest_survey_year():
    """Return the latest year an administrator may declare for an import."""
    return datetime.now(PHILIPPINE_TIME).year


def is_supported_survey_year(value):
    """Accept historical years from the pilot year through the current year."""
    return (
        type(value) is int
        and MIN_SURVEY_YEAR <= value <= latest_survey_year()
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
