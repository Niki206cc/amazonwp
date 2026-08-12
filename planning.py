import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")


def parse_publish_times(value):
    result = []
    seen = set()
    for raw in (value or "09:00").replace(";", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            hh, mm = raw.split(":", 1)
            hour = int(hh)
            minute = int(mm)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            normalized = f"{hour:02d}:{minute:02d}"
        except (ValueError, TypeError):
            continue
        if normalized not in seen:
            seen.add(normalized)
            result.append((normalized, hour, minute))
    return sorted(result) or [("09:00", 9, 0)]


def parse_publish_days(value):
    days = set()
    for raw in (value or "0,1,2,3,4,5,6").split(","):
        try:
            day = int(raw.strip())
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            days.add(day)
    return days or set(range(7))


def next_publish_slots(settings, count, now_dt=None):
    if count <= 0 or settings.get("scheduler_enabled") != "1":
        return []

    now_dt = now_dt or datetime.now(TZ)
    days = parse_publish_days(settings.get("publish_days"))
    times = parse_publish_times(settings.get("publish_time"))
    slots = []

    for offset in range(0, 120):
        date = (now_dt + timedelta(days=offset)).date()
        if date.weekday() not in days:
            continue
        for _, hour, minute in times:
            candidate = datetime(date.year, date.month, date.day, hour, minute, tzinfo=TZ)
            if candidate <= now_dt:
                continue
            slots.append(candidate)
            if len(slots) >= count:
                return slots
    return slots


STOPWORDS = {
    "amazon", "prodotto", "nuovo", "nuova", "con", "per", "della", "delle", "degli", "allo", "alla",
    "smart", "versione", "modello", "kit", "set", "nero", "nera", "bianco", "bianca",
}


def _title_tokens(value):
    words = re.findall(r"[a-zà-ÿ0-9]+", (value or "").lower())
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def are_similar_products(previous, current):
    prev_category = str(previous.get("category") or "").strip().lower()
    curr_category = str(current.get("category") or "").strip().lower()
    if prev_category and curr_category and prev_category == curr_category:
        return True

    prev_tokens = _title_tokens(previous.get("product_title", ""))
    curr_tokens = _title_tokens(current.get("product_title", ""))
    common = prev_tokens & curr_tokens
    return len(common) >= 2
