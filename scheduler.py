from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Rome")
scheduler = BackgroundScheduler(timezone=TZ)


def configure_scheduler(settings, callback):
    if scheduler.get_job("publish_next"):
        scheduler.remove_job("publish_next")
    if settings.get("scheduler_enabled") != "1":
        return
    hh, mm = (settings.get("publish_time") or "09:00").split(":", 1)
    days = settings.get("publish_days") or "0,1,2,3,4,5,6"
    scheduler.add_job(
        callback,
        "cron",
        id="publish_next",
        day_of_week=days,
        hour=int(hh),
        minute=int(mm),
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
