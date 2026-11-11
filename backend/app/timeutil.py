"""Every timestamp is turned into timezone-aware UTC on the way in."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import settings

UTC = timezone.utc


def to_utc(value: str | float | int | datetime, default_tz: str | None = None) -> datetime:
    """Parse ISO strings, '2026-11-10 09:00:01,123', epoch seconds/millis, or datetimes.

    A timestamp without a zone is read in DEFAULT_TZ (UTC unless configured), then converted.
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", value.strip())):
        num = float(value)
        if num > 1e11:  # milliseconds
            num /= 1000
        return datetime.fromtimestamp(num, UTC)
    else:
        s = value.strip().replace(",", ".")
        s = re.sub(r"Z$", "+00:00", s)
        s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)  # +0530 -> +05:30
        dt = datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s)
    if dt.tzinfo is None:
        name = default_tz or settings.default_tz
        # ZoneInfo needs the "tzdata" package on Windows; UTC never does.
        dt = dt.replace(tzinfo=UTC if name.upper() == "UTC" else ZoneInfo(name))
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
