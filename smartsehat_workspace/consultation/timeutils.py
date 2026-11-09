"""Local calendar dates for care; persisted timestamps remain UTC."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def care_timezone() -> ZoneInfo:
    from .clinical import config
    name = config().get("timezone", "Asia/Karachi")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, TypeError) as exc:
        raise ValueError("Configure a valid consultation timezone and install tzdata.") from exc


def local_today(at: datetime | None = None) -> date:
    stamp = at if at is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("A calendar reference requires a timezone-aware timestamp.")
    return stamp.astimezone(care_timezone()).date()


def display_time(stamp: str) -> str:
    value = datetime.fromisoformat(stamp)
    if value.tzinfo is None:
        return stamp + " (timezone not recorded)"
    return value.astimezone(care_timezone()).strftime("%d %b %Y, %H:%M") + " (" + str(care_timezone()) + ")"
