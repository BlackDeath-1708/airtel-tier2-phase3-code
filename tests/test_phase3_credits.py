import time

import pytest

from phase3 import config
from phase3.globalping import CreditsExhausted, GlobalpingClient, RequestFailed
from phase3.runner import run_items
from tests.test_phase3_probe_wait import _mumbai_items

LOCATION = {"city": "Pune", "asn": 55352}


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    """Every measurement create is answered with the same 429 body."""

    def __init__(self, body):
        self.body = body
        self.posts = 0

    def post(self, url, json, headers, timeout):
        self.posts += 1
        return FakeResponse(429, self.body)


def _client(session):
    return GlobalpingClient(tokens=["token-a", "token-b"], session=session, sleep=lambda seconds: None)


CREDITS_BODY = {"error": {"type": "insufficient_credits"}}


class TokenSession:
    """Answers each token from its own table, so one token can be out of credits while another works."""

    def __init__(self, answers):
        self.answers = answers  # token -> (status code, body)
        self.posts = []

    def post(self, url, json, headers, timeout):
        token = headers["Authorization"].removeprefix("Bearer ")
        self.posts.append(token)
        status, body = self.answers[token]
        return FakeResponse(status, body)


def test_a_token_out_of_credits_is_skipped_for_the_next_one():
    session = TokenSession({"token-a": (429, CREDITS_BODY), "token-b": (202, {"id": "m1"})})

    assert _client(session)._create({"type": "traceroute"}) == "m1"
    assert session.posts == ["token-a", "token-b"]


def test_run_stops_only_when_every_token_is_out_of_credits():
    session = TokenSession({"token-a": (429, CREDITS_BODY), "token-b": (429, CREDITS_BODY)})

    with pytest.raises(CreditsExhausted):
        _client(session).traceroute("8.8.8.8", LOCATION)

    assert session.posts == ["token-a", "token-b"]


def test_other_rate_limits_are_still_retried_before_failing():
    session = FakeSession({"error": {"type": "rate_limit_exceeded"}})

    with pytest.raises(RequestFailed):
        _client(session).traceroute("8.8.8.8", LOCATION)

    assert session.posts == config.MAX_RETRIES


class CreditsGoneClient:
    def unavailable(self, key):
        return None

    def traceroute(self, target, location):
        raise CreditsExhausted(time.monotonic() + 600)


class RecoveringClient:
    """Every token is out of credits until `dry_until`, then measurements work again."""

    def __init__(self, dry_until):
        self.dry_until = dry_until

    def unavailable(self, key):
        return None

    def traceroute(self, target, location):
        if time.monotonic() < self.dry_until:
            raise CreditsExhausted(self.dry_until)
        return {"probe": {}, "status": "finished", "hops": []}


def test_run_pauses_when_credits_run_out_and_auto_resume_is_off(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "CREDITS_AUTO_RESUME", False)

    status, done = run_items(
        _mumbai_items(),
        client=CreditsGoneClient(),
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=2,
    )

    assert status == "paused"
    assert done == 0
    assert "insufficient_credits" in capsys.readouterr().out


def test_run_waits_for_credits_and_finishes_by_itself(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    items = _mumbai_items()

    status, done = run_items(
        items,
        client=RecoveringClient(dry_until=time.monotonic() + 0.3),
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=2,
    )

    assert status == "complete"
    assert done == len(items)
    assert "Trying again automatically" in capsys.readouterr().out
