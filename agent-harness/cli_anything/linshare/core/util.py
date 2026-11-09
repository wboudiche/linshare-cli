"""Input helpers: expiry dates, email lists, safe download paths."""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from cli_anything.linshare.core.errors import UsageError

_RELATIVE = re.compile(r"^(\d+)([dwm])$")
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_expires(value: str, today: date) -> date:
    """Parse `7d`, `2w`, `1m` (30 days) or an ISO date; must be in the future."""
    text = (value or "").strip().lower()
    match = _RELATIVE.match(text)
    if match:
        result = today + timedelta(days=int(match.group(1)) * _UNIT_DAYS[match.group(2)])
    else:
        try:
            result = date.fromisoformat(text)
        except ValueError:
            raise UsageError(
                f"Invalid expiry '{value}': use a number of days/weeks/months (7d, 2w, 1m) or a date (YYYY-MM-DD)"
            ) from None
    if result <= today:
        raise UsageError(f"Expiry '{value}' must be in the future")
    return result


def normalize_emails(values: list[str]) -> list[str]:
    """Trim, lowercase, de-duplicate (first seen wins) and validate addresses."""
    result: list[str] = []
    for raw in values:
        email = raw.strip().lower()
        if not _EMAIL.match(email):
            raise UsageError(f"Invalid email address: '{raw.strip()}'")
        if email not in result:
            result.append(email)
    if not result:
        raise UsageError("At least one email address is required")
    return result


def is_external(email: str, own_email: str) -> bool:
    """Heuristic: an address is external when its domain differs from the user's."""
    return email.rsplit("@", 1)[-1].lower() != own_email.rsplit("@", 1)[-1].lower()


def safe_target_path(directory: Path, filename: str) -> Path:
    """Path inside `directory` for `filename` that never overwrites an existing file."""
    name = Path(filename.replace("\\", "/")).name or "download"
    candidate = directory / name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def iso_from_ms(value) -> str | None:
    """LinShare dates come as epoch milliseconds; show them as ISO-8601 UTC."""
    if value is None or isinstance(value, str):
        return value
    from datetime import datetime, timezone

    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def end_of_day_ms(day: date) -> int:
    """Epoch milliseconds of 23:59:59 local time on `day`, so the date the user approved is the date shown."""
    from datetime import datetime, time

    return int(datetime.combine(day, time(23, 59, 59)).astimezone().timestamp()) * 1000
