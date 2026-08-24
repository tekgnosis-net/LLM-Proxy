from datetime import datetime, timedelta, timezone
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from app.backup_scheduler import build_trigger, next_hhmm

TZ = timezone(timedelta(hours=10))
NOW = datetime(2026, 8, 24, 12, 0, tzinfo=TZ)


def test_daily_and_weekly_are_cron():
    t = build_trigger({"frequency": {"kind": "daily"}, "time": "03:15"}, NOW)
    assert isinstance(t, CronTrigger)
    w = build_trigger({"frequency": {"kind": "weekly", "weekday": 2}, "time": "04:00"}, NOW)
    assert isinstance(w, CronTrigger)


def test_every_n_days_is_interval_starting_next_hhmm():
    t = build_trigger({"frequency": {"kind": "every_n_days", "n": 3}, "time": "03:00"}, NOW)
    assert isinstance(t, IntervalTrigger)
    assert t.interval == timedelta(days=3)
    assert t.start_date == datetime(2026, 8, 25, 3, 0, tzinfo=NOW.tzinfo)  # 03:00 already past today


def test_next_hhmm_today_when_future():
    assert next_hhmm(NOW, "13:30") == NOW.replace(hour=13, minute=30, second=0, microsecond=0)
    assert next_hhmm(NOW, "11:00") == (NOW + timedelta(days=1)).replace(hour=11, minute=0, second=0, microsecond=0)
