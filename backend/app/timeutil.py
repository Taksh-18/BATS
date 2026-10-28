from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def today() -> date:
    """Calendar date in the configured hospital time zone (not the server's local zone)."""
    return datetime.now(ZoneInfo(settings.timezone)).date()
