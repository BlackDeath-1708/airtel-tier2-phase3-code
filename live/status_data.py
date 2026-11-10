"""What the live scan page shows, worked out from the status summary the scan publishes.

Kept Streamlit-free so it can be unit-tested. The lock rule matches phase3/sync.py.
"""

import datetime

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
LOCK_STALE_SEC = 45 * 60


def progress_percent(done, total):
    if not total:
        return 0.0
    return min(100.0, 100.0 * done / total)


def to_ist(iso_utc):
    if not iso_utc:
        return "n/a"
    moment = datetime.datetime.fromisoformat(iso_utc)
    return moment.astimezone(IST).strftime("%d %b %Y, %H:%M:%S IST")


def scan_health(status, now):
    """running: a runner holds the lock and its heartbeat is fresh. stalled: it holds the lock but went quiet.

    paused and complete come from the run's own status. idle: nothing is running and nothing is paused.
    """
    if status.get("run_status") == "complete":
        return "complete"
    heartbeat = status.get("heartbeat")
    if status.get("running_on") and heartbeat:
        age = (now - datetime.datetime.fromisoformat(heartbeat)).total_seconds()
        return "running" if age < LOCK_STALE_SEC else "stalled"
    if status.get("run_status") == "paused":
        return "paused"
    return "idle"
