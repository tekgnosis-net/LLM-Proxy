"""APScheduler wiring for the two backup tiers (spec §5)."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import Optional
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger("uvicorn.error")
_JOB_IDS = {"config": "backup-config", "logs": "backup-logs"}
_scheduler = None


def set_scheduler(s) -> None:
    global _scheduler; _scheduler = s


def get_scheduler():
    return _scheduler


def next_hhmm(now: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return cand if cand > now else cand + timedelta(days=1)


def build_trigger(tier_settings: dict, now: datetime):
    freq = tier_settings["frequency"]
    h, m = (int(x) for x in tier_settings["time"].split(":"))
    if freq["kind"] == "daily":
        return CronTrigger(hour=h, minute=m)
    if freq["kind"] == "weekly":
        return CronTrigger(day_of_week=freq["weekday"], hour=h, minute=m)
    return IntervalTrigger(days=freq["n"], start_date=next_hhmm(now, tier_settings["time"]))


async def register_backup_jobs(sched, store, engine_factory) -> None:
    """Add/replace/remove the two backup jobs to match current settings."""
    settings = await store.get_settings()
    now = datetime.now().astimezone()
    for tier, job_id in _JOB_IDS.items():
        if sched.get_job(job_id):
            sched.remove_job(job_id)
        if not settings[tier]["enabled"]:
            continue

        async def job(tier=tier):
            try:
                eng = engine_factory()
                out = await (eng.run_config() if tier == "config" else eng.run_logs())
                if not out.get("ok"):
                    log.warning("scheduled %s backup failed: %s", tier, out.get("error"))
            except Exception:
                log.exception("scheduled %s backup crashed", tier)

        sched.add_job(job, build_trigger(settings[tier], now), id=job_id, replace_existing=True)


def next_fire(tier: str) -> Optional[str]:
    s = get_scheduler()
    if not s: return None
    j = s.get_job(_JOB_IDS[tier])
    return j.next_run_time.isoformat() if j and j.next_run_time else None
