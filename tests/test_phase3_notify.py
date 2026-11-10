import pytest
import requests

from phase3 import notify
from phase3 import sync

RUN_URL = "https://github.com/example/scan/actions/runs/1"
WEBHOOK = "https://discord.com/api/webhooks/123/SECRET-TOKEN-42"
STATUS = {"run_status": "paused", "units_done": 116855, "units_total": 1430376}


def test_message_for_a_pause_says_the_scan_continues_and_shows_the_progress():
    text = notify.build_message("success", False, STATUS, RUN_URL)

    assert "paused at its time limit" in text
    assert "116,855 of 1,430,376 units (8.2%)" in text
    assert RUN_URL in text


def test_message_for_a_finished_scan_says_complete():
    text = notify.build_message("success", True, dict(STATUS, run_status="complete"), RUN_URL)

    assert "COMPLETE" in text


def test_message_for_a_failed_step_says_stopped_with_an_error():
    text = notify.build_message("failure", False, None, RUN_URL)

    assert "STOPPED with an error (failure)" in text
    assert "units" not in text


def test_send_posts_the_message_to_the_webhook_and_accepts_discord_no_content_reply(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json))
        return type("Response", (), {"status_code": 204})()

    monkeypatch.setattr(notify.requests, "post", fake_post)

    notify.send("hello", WEBHOOK)

    assert calls == [(WEBHOOK, {"content": "hello"})]


def test_send_fails_on_a_bad_answer(monkeypatch):
    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: type("R", (), {"status_code": 404})())

    with pytest.raises(RuntimeError, match="HTTP 404"):
        notify.send("hello", WEBHOOK)


def test_send_refuses_to_run_without_the_webhook():
    with pytest.raises(ValueError):
        notify.send("hello", "")


def test_a_failed_send_never_prints_the_webhook(monkeypatch, capsys):
    def failing_post(url, json, timeout):
        raise requests.ConnectionError(f"could not reach {url}")

    monkeypatch.setattr(notify.requests, "post", failing_post)
    monkeypatch.setattr(sync, "read_status", lambda: STATUS)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)

    code = notify.main(["--outcome", "success", "--finished", "false", "--run-url", RUN_URL])

    output = capsys.readouterr()
    assert code == 1
    assert "SECRET-TOKEN-42" not in output.out + output.err
    assert "ConnectionError" in output.err


def test_main_sends_the_message_with_the_scan_status(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, webhook: sent.append((text, webhook)))
    monkeypatch.setattr(sync, "read_status", lambda: STATUS)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", WEBHOOK)

    assert notify.main(["--outcome", "success", "--finished", "false", "--run-url", RUN_URL]) == 0

    assert sent and "116,855" in sent[0][0] and sent[0][1] == WEBHOOK
