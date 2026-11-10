import threading
import time
import json
import os

from phase3 import checkpoint
from phase3.plan import build_plan, first_run_items
from phase3.globalping import GlobalpingClient, RequestFailed
from phase3.runner import run_items


class FakeClient:
    def __init__(self, on_call=None):
        self.calls = []
        self.on_call = on_call

    def traceroute(self, target, location):
        self.calls.append((target, location["asn"]))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        return {
            "probe": {"city": location["city"], "asn": location["asn"]},
            "status": "finished",
            "hops": [{"resolvedAddress": "10.0.0.1"}],
        }


def mumbai_plan():
    return build_plan(
        [
            {"country": "IN", "city": "Mumbai", "asn": 24560, "network": "Bharti Airtel"},
            {"country": "IN", "city": "Mumbai", "asn": 31898, "network": "Oracle"},
        ]
    )


def run(plan, tmp_path, targets, client, skip=frozenset()):
    return run_items(
        first_run_items(plan, targets),
        client=client,
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(skip),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
    )


def test_run_writes_one_output_per_ip_city_and_records_progress(tmp_path):
    run(mumbai_plan(), tmp_path, ["1.1.1.1", "2.2.2.2"], FakeClient())

    results_root = str(tmp_path / "results")
    assert os.path.exists(checkpoint.unit_output_path(results_root, "1.1.1.1", "Mumbai"))
    with open(tmp_path / "progress.jsonl", encoding="utf-8") as fh:
        keys = [json.loads(line)["unit"] for line in fh]
    assert keys == ["1.1.1.1|Mumbai", "2.2.2.2|Mumbai"]


def test_each_unit_stores_all_nine_traceroutes(tmp_path):
    run(mumbai_plan(), tmp_path, ["1.1.1.1"], FakeClient())

    path = checkpoint.unit_output_path(str(tmp_path / "results"), "1.1.1.1", "Mumbai")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    total = sum(len(r["measurements"]) for r in record["rounds"])
    assert total == 9


def test_pause_then_resume_finishes_remaining_units_without_duplicates(tmp_path):
    pause_file = tmp_path / "PAUSE"
    targets = ["1.1.1.1", "2.2.2.2", "3.3.3.3"]

    def pause_after_second_unit(call_count):
        # Two probes split 9 iterations as 5 + 4, and each traceroute is one
        # request, so a unit makes 9 requests. Request 18 ends the second unit.
        if call_count == 18:
            pause_file.write_text("")

    first = FakeClient(on_call=pause_after_second_unit)
    run(mumbai_plan(), tmp_path, targets, first)
    pause_file.unlink()

    with open(tmp_path / "progress.jsonl", encoding="utf-8") as fh:
        done = [json.loads(line)["unit"] for line in fh]
    assert done == ["1.1.1.1|Mumbai", "2.2.2.2|Mumbai"]

    skip = checkpoint.completed_units_to_skip(
        str(tmp_path / "progress.jsonl"), safety_window=0, results_root=str(tmp_path / "results")
    )
    second = FakeClient()
    run(mumbai_plan(), tmp_path, targets, second, skip=skip)

    assert len(second.calls) == 9
    assert {call[0] for call in second.calls} == {"3.3.3.3"}


def test_concurrent_run_stores_every_unit_once(tmp_path):
    targets = [f"10.0.0.{n}" for n in range(1, 11)]
    status, done = run_items(
        first_run_items(mumbai_plan(), targets),
        client=FakeClient(),
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=4,
    )

    with open(tmp_path / "progress.jsonl", encoding="utf-8") as fh:
        keys = [json.loads(line)["unit"] for line in fh]
    assert status == "complete"
    assert done == 10
    assert sorted(keys) == sorted(f"{ip}|Mumbai" for ip in targets)


def test_pause_before_start_leaves_queued_unit_unrun(tmp_path):
    pause_file = tmp_path / "PAUSE"

    def pause_after_second_unit(call_count):
        if call_count == 18:
            pause_file.write_text("")

    status, done = run(mumbai_plan(), tmp_path, ["1.1.1.1", "2.2.2.2", "3.3.3.3"],
                       FakeClient(on_call=pause_after_second_unit))

    assert status == "paused"
    assert done == 2
    assert not os.path.exists(checkpoint.unit_output_path(str(tmp_path / "results"), "3.3.3.3", "Mumbai"))


def test_failed_request_is_retried_and_the_run_still_completes(tmp_path):
    class FailOnceClient(FakeClient):
        failed = False

        def traceroute(self, target, location):
            if target == "2.2.2.2" and not self.failed:
                self.failed = True
                raise RequestFailed("boom")
            return super().traceroute(target, location)

    status, done = run(mumbai_plan(), tmp_path, ["1.1.1.1", "2.2.2.2", "3.3.3.3"], FailOnceClient())

    with open(tmp_path / "progress.jsonl", encoding="utf-8") as fh:
        keys = [json.loads(line)["unit"] for line in fh]
    assert status == "complete"
    assert done == 3
    assert sorted(keys) == ["1.1.1.1|Mumbai", "2.2.2.2|Mumbai", "3.3.3.3|Mumbai"]


def test_supervisor_restarts_after_a_crash_until_complete(tmp_path, monkeypatch):
    import argparse

    from phase3 import __main__ as cli
    from phase3.runs import run_paths, write_state

    monkeypatch.setattr(cli.config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(cli.config, "MANIFEST_FILE", str(tmp_path / "run_manifest.json"))
    monkeypatch.setattr(cli.config, "PAUSE_FILE", str(tmp_path / "PAUSE"))
    monkeypatch.setattr(cli, "_new_manifest", lambda limit, cities: {"targets_sha256": "x"})
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    calls = []

    def fake_execute(number, manifest, workers):
        calls.append(number)
        if len(calls) == 1:
            raise RuntimeError("simulated crash")
        write_state(run_paths(str(tmp_path), number), "complete", 1)

    monkeypatch.setattr(cli, "_execute_run", fake_execute)

    cli.cmd_supervise(argparse.Namespace(workers=1, limit_ips=None, cities=None))

    assert calls == [1, 1]


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    """Answers POST with the queued status codes and records the bearer token used each time."""

    def __init__(self, post_statuses):
        self._post_statuses = list(post_statuses)
        self.tokens_used = []

    def post(self, url, json, headers, timeout):
        self.tokens_used.append(headers["Authorization"])
        status = self._post_statuses.pop(0)
        return FakeResponse(status, {"id": "m1"} if status == 202 else None)

    def get(self, url, headers, timeout):
        return FakeResponse(200, {"status": "finished", "results": [{"probe": {"city": "Mumbai", "asn": 24560},
                                                                     "result": {"status": "finished", "hops": []}}]})


def test_tokens_come_only_from_the_environment(monkeypatch):
    from phase3 import __main__ as cli

    monkeypatch.delenv("GLOBALPING_TOKENS", raising=False)
    assert cli._tokens_from_env() == []

    monkeypatch.setenv("GLOBALPING_TOKENS", " token-a ,, token-b ")
    assert cli._tokens_from_env() == ["token-a", "token-b"]


def test_rate_limited_token_is_skipped_for_the_next_request():
    session = FakeSession([429, 202])
    client = GlobalpingClient(["token-a", "token-b"], session=session, sleep=lambda _: None)

    client.traceroute("1.1.1.1", {"city": "Mumbai", "asn": 24560})

    assert session.tokens_used == ["Bearer token-a", "Bearer token-b"]


def test_network_error_is_retried_instead_of_stopping_the_run():
    import requests

    class FlakySession(FakeSession):
        def post(self, url, json, headers, timeout):
            if not self.tokens_used:
                self.tokens_used.append(headers["Authorization"])
                raise requests.ConnectionError("dns down")
            return super().post(url, json, headers, timeout)

    session = FlakySession([202])
    client = GlobalpingClient(["token-a"], session=session, sleep=lambda _: None)

    result = client.traceroute("1.1.1.1", {"city": "Mumbai", "asn": 24560})

    assert result["status"] == "finished"
    assert len(session.tokens_used) == 2


class OverlapClient:
    """Each traceroute takes a moment, and the client records how many are in flight at the same time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.in_flight = 0
        self.most_in_flight = 0

    def traceroute(self, target, location):
        with self.lock:
            self.in_flight += 1
            self.most_in_flight = max(self.most_in_flight, self.in_flight)
        time.sleep(0.2)
        with self.lock:
            self.in_flight -= 1
        return {"probe": {"city": location["city"], "asn": location["asn"]}, "status": "finished", "hops": []}


def test_traceroutes_of_one_round_run_at_the_same_time(tmp_path):
    client = OverlapClient()

    run(mumbai_plan(), tmp_path, ["1.1.1.1"], client)

    assert client.most_in_flight >= 2
    path = checkpoint.unit_output_path(str(tmp_path / "results"), "1.1.1.1", "Mumbai")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    assert [r["round"] for r in record["rounds"]] == sorted(r["round"] for r in record["rounds"])
    assert sum(len(r["measurements"]) for r in record["rounds"]) == 9
