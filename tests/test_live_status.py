import datetime

from live.github_source import fetch_asset, fetch_view
from live.status_data import progress_percent, scan_health, to_ist

NOW = datetime.datetime(2026, 11, 8, 9, 0, tzinfo=datetime.timezone.utc)


def _ago(minutes):
    return (NOW - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def test_progress_percent_handles_an_empty_total():
    assert progress_percent(5, 0) == 0.0
    assert progress_percent(50, 200) == 25.0


def test_to_ist_shows_utc_times_in_india_time():
    assert to_ist("2026-11-08T08:15:47+00:00") == "08 Nov 2026, 13:45:47 IST"
    assert to_ist(None) == "n/a"


def test_health_is_running_while_the_heartbeat_is_fresh():
    status = {"running_on": "github", "heartbeat": _ago(3), "run_status": "running"}
    assert scan_health(status, NOW) == "running"


def test_health_is_stalled_when_the_lock_holder_has_gone_quiet():
    status = {"running_on": "github", "heartbeat": _ago(60), "run_status": "running"}
    assert scan_health(status, NOW) == "stalled"


def test_health_reports_paused_complete_and_idle():
    assert scan_health({"run_status": "paused", "running_on": None}, NOW) == "paused"
    assert scan_health({"run_status": "complete", "running_on": None}, NOW) == "complete"
    assert scan_health({"run_status": None, "running_on": None}, NOW) == "idle"


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, release, status_body):
        self.release = release
        self.status_body = status_body
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append((url, headers))
        if "releases/tags" in url:
            return FakeResponse(self.release)
        return FakeResponse(self.status_body)


def test_fetch_view_reads_the_view_asset_with_the_token():
    release = {"assets": [{"name": "phase3-state.tar.gz", "url": "https://api.example/a"},
                          {"name": "view.json", "url": "https://api.example/view"}]}
    session = FakeSession(release, {"status": {"units_done": 10}})

    assert fetch_view("owner/repo", "secret-token", session=session) == {"status": {"units_done": 10}}
    assert session.calls[0][1]["Authorization"] == "Bearer secret-token"
    assert session.calls[1][0] == "https://api.example/view"


def test_fetch_asset_says_when_the_file_is_missing():
    session = FakeSession({"assets": []}, {})

    try:
        fetch_asset("owner/repo", "secret-token", "view.json", session=session)
    except LookupError as error:
        assert "view.json" in str(error)
    else:
        raise AssertionError("expected LookupError")
