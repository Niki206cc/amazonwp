from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
scheduler = BackgroundScheduler(timezone=TZ)


def _remove_publish_jobs():
    for job in scheduler.get_jobs():
        if job.id == "publish_next" or job.id.startswith("publish_next_"):
            scheduler.remove_job(job.id)


def _parse_publish_times(value):
    raw_times = (value or "09:00").replace(";", ",").split(",")
    result = []
    seen = set()
    for raw in raw_times:
        value = raw.strip()
        if not value:
            continue
        try:
            hh, mm = value.split(":", 1)
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
    return result or [("09:00", 9, 0)]


def configure_scheduler(settings, callback):
    _remove_publish_jobs()

    if settings.get("scheduler_enabled") != "1":
        return

    days = settings.get("publish_days") or "0,1,2,3,4,5,6"
    publish_times = _parse_publish_times(settings.get("publish_time"))

    for index, (label, hour, minute) in enumerate(publish_times, 1):
        scheduler.add_job(
            callback,
            "cron",
            id=f"publish_next_{index}",
            name=f"Pubblicazione articolo {label}",
            day_of_week=days,
            hour=hour,
            minute=minute,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
