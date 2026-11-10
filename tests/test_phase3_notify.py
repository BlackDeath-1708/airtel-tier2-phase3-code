import pytest
import requests

from phase3 import notify
from phase3 import sync

RUN_URL = "https://github.com/example/scan/actions/runs/1"
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


def test_send_passes_the_number_text_and_key_and_fails_on_a_bad_answer(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, params))
        return type("Response", (), {"status_code": 200})()

    monkeypatch.setattr(notify.requests, "get", fake_get)

    notify.send("hello", "917700000000", "key-123")

    assert calls[0][1] == {"phone": "917700000000", "text": "hello", "apikey": "key-123"}


def test_send_refuses_to_run_without_the_number_or_the_key():
    with pytest.raises(ValueError):
        notify.send("hello", "", "key-123")


def test_a_failed_send_never_prints_the_key(monkeypatch, capsys):
    def failing_get(url, params, timeout):
        raise requests.ConnectionError(f"could not reach {url}?apikey={params['apikey']}")

    monkeypatch.setattr(notify.requests, "get", failing_get)
    monkeypatch.setattr(sync, "read_status", lambda: STATUS)
    monkeypatch.setenv("WHATSAPP_PHONE", "917700000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "SECRET-KEY-42")

    code = notify.main(["--outcome", "success", "--finished", "false", "--run-url", RUN_URL])

    output = capsys.readouterr()
    assert code == 1
    assert "SECRET-KEY-42" not in output.out + output.err
    assert "ConnectionError" in output.err


def test_main_sends_the_message_with_the_scan_status(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send", lambda text, phone, key: sent.append((text, phone, key)))
    monkeypatch.setattr(sync, "read_status", lambda: STATUS)
    monkeypatch.setenv("WHATSAPP_PHONE", "917700000000")
    monkeypatch.setenv("CALLMEBOT_APIKEY", "key-123")

    assert notify.main(["--outcome", "success", "--finished", "false", "--run-url", RUN_URL]) == 0

    assert sent and "116,855" in sent[0][0]
