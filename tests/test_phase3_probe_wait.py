import json
import time

import pytest

from phase3 import checkpoint, config
from phase3.globalping import GlobalpingClient, ProbeUnavailable
from phase3.plan import build_plan, first_run_items
from phase3.runner import run_items

ERODE = {"city": "Erode", "asn": 135801}


class Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class ScriptedSession:
    """Answers each POST with the next scripted status; every POST is counted."""

    def __init__(self, post_statuses):
        self._post_statuses = list(post_statuses)
        self.posts = 0

    def post(self, url, json, headers, timeout):
        self.posts += 1
        status = self._post_statuses.pop(0)
        return Response(status, {"id": "m1"} if status == 202 else None)

    def get(self, url, headers, timeout):
        return Response(200, {"status": "finished", "results": [
            {"probe": {"city": "Erode", "asn": 135801}, "result": {"status": "finished", "hops": []}},
        ]})


def test_missing_probe_is_not_asked_again_during_its_cooldown(monkeypatch):
    monkeypatch.setattr(config, "PROBE_UNAVAILABLE_COOLDOWN_SEC", 1800)
    now = [0.0]
    session = ScriptedSession([422])
    client = GlobalpingClient(["token-a"], session=session, sleep=lambda _: None, clock=lambda: now[0])

    with pytest.raises(ProbeUnavailable):
        client.traceroute("1.1.1.1", ERODE)
    now[0] = 600.0
    with pytest.raises(ProbeUnavailable):
        client.traceroute("1.1.1.1", ERODE)

    assert session.posts == 1


def test_probe_is_checked_again_once_the_cooldown_ends_and_clears_when_it_returns(monkeypatch):
    monkeypatch.setattr(config, "PROBE_UNAVAILABLE_COOLDOWN_SEC", 1800)
    now = [0.0]
    session = ScriptedSession([422, 202])
    client = GlobalpingClient(["token-a"], session=session, sleep=lambda _: None, clock=lambda: now[0])
    with pytest.raises(ProbeUnavailable):
        client.traceroute("1.1.1.1", ERODE)

    now[0] = 1800.0
    result = client.traceroute("1.1.1.1", ERODE)

    assert session.posts == 2
    assert result["status"] == "finished"
    assert client.unavailable(("Erode", 135801)) is None


def _mumbai_items():
    plan = build_plan([
        {"country": "IN", "city": "Mumbai", "asn": 24560, "network": "Bharti Airtel"},
        {"country": "IN", "city": "Mumbai", "asn": 31898, "network": "Oracle"},
    ])
    return first_run_items(plan, ["1.1.1.1", "2.2.2.2", "3.3.3.3"])


class PairMissingClient:
    """Like the Globalping client for one probe pair that is missing until `back_at` (monotonic seconds)."""

    def __init__(self, missing_key, back_at):
        self.missing_key = missing_key
        self.back_at = back_at
        self.since = time.monotonic()
        self.calls = []

    def traceroute(self, target, location):
        key = (location["city"], location["asn"])
        if key == self.missing_key and time.monotonic() < self.back_at:
            raise ProbeUnavailable(key)
        self.calls.append((target, key))
        return {"probe": {"city": location["city"], "asn": location["asn"]},
                "status": "finished", "hops": [{"resolvedAddress": "10.0.0.1"}]}

    def unavailable(self, key):
        if key == self.missing_key and time.monotonic() < self.back_at:
            return {"since": self.since, "retry_at": self.back_at}
        return None


def _run(tmp_path, client):
    return run_items(
        _mumbai_items(),
        client=client,
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=2,
    )


def _measurement_status(tmp_path, ip, asn):
    path = checkpoint.unit_output_path(str(tmp_path / "results"), ip, "Mumbai")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    statuses = {m["selector"]["asn"]: m["status"] for r in record["rounds"] for m in r["measurements"]}
    return statuses[asn]


def test_parked_units_finish_once_the_missing_probe_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    client = PairMissingClient(("Mumbai", 31898), back_at=time.monotonic() + 0.2)

    status, done = _run(tmp_path, client)

    assert status == "complete"
    assert done == 3
    with open(tmp_path / "progress.jsonl", encoding="utf-8") as fh:
        keys = [json.loads(line)["unit"] for line in fh]
    assert sorted(keys) == ["1.1.1.1|Mumbai", "2.2.2.2|Mumbai", "3.3.3.3|Mumbai"]
    assert _measurement_status(tmp_path, "1.1.1.1", 31898) == "finished"


def test_a_probe_missing_past_the_give_up_limit_is_recorded_as_no_probe_and_the_run_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    monkeypatch.setattr(config, "PROBE_GIVE_UP_AFTER_SEC", 0.05)
    client = PairMissingClient(("Mumbai", 31898), back_at=float("inf"))

    status, done = _run(tmp_path, client)

    assert status == "complete"
    assert done == 3
    assert _measurement_status(tmp_path, "1.1.1.1", 31898) == "no_probe"
    assert _measurement_status(tmp_path, "1.1.1.1", 24560) == "finished"
    assert all(key != ("Mumbai", 31898) for _, key in client.calls)
