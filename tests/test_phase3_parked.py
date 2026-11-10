import collections
import time

import pandas as pd

from gui import phase3_data as p3
from phase3 import config
from phase3.parked import ParkedStatus
from phase3.runner import run_items
from tests.test_phase3_probe_wait import PairMissingClient, _mumbai_items


def _run_with_parked_dir(tmp_path, client):
    return run_items(
        _mumbai_items(),
        client=client,
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=2,
        parked_dir=str(tmp_path),
    )


def test_a_parked_probe_is_logged_missing_then_back_and_the_snapshot_is_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    client = PairMissingClient(("Mumbai", 31898), back_at=time.monotonic() + 0.2)

    status, done = _run_with_parked_dir(tmp_path, client)

    assert status == "complete" and done == 3
    events = p3.read_events(str(tmp_path))
    kinds = [event["event"] for event in reversed(events)]
    assert kinds[0] == "missing"
    assert "back" in kinds
    assert all(event["asn"] == 31898 for event in events)
    assert p3.read_parked(str(tmp_path))["pairs"] == []


def test_a_probe_that_never_returns_is_listed_with_its_parked_units_and_then_given_up(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    monkeypatch.setattr(config, "PROBE_GIVE_UP_AFTER_SEC", 0.05)
    client = PairMissingClient(("Mumbai", 31898), back_at=float("inf"))

    _run_with_parked_dir(tmp_path, client)

    kinds = [event["event"] for event in reversed(p3.read_events(str(tmp_path)))]
    assert kinds[0] == "missing"
    assert kinds[-1] == "gave_up"
    assert p3.gave_up_count(str(tmp_path)) == 1


def test_snapshot_lists_each_parked_unit_with_the_probe_it_waits_for(tmp_path):
    waiting = {("Erode", 135801): collections.deque([
        {"ip": "10.0.0.1", "city": "Delhi"},
        {"ip": "10.0.0.2", "city": "Delhi"},
    ])}
    client = PairMissingClient(("Erode", 135801), back_at=float("inf"))
    parked = ParkedStatus(str(tmp_path))

    parked.snapshot(waiting, client, force=True)
    data = p3.read_parked(str(tmp_path))

    assert data["pairs"][0]["probe_city"] == "Erode"
    assert data["pairs"][0]["parked_units"] == 2
    assert data["pairs"][0]["next_check_at"] is None  # no scheduled recheck while the cooldown never ends
    assert [u["ip"] for u in data["units"]] == ["10.0.0.1", "10.0.0.2"]
    assert all(u["waiting_for_probe_city"] == "Erode" for u in data["units"])


def test_parked_units_frame_adds_the_isp_of_each_target():
    parked = {"pairs": [], "units": [{
        "ip": "10.0.0.1", "city": "Delhi",
        "waiting_for_probe_city": "Erode", "waiting_for_asn": 135801,
    }]}
    targets = pd.DataFrame({"Target IP": ["10.0.0.1"], "City": ["Delhi"], "ISP": ["BSNL"], "Tier": ["telecom"]})

    frame = p3.parked_units_frame(parked, targets)

    assert frame.iloc[0]["ISP"] == "BSNL"
    assert frame.iloc[0]["Waiting for probe city"] == "Erode"


def test_events_are_listed_newest_first_with_readable_labels(tmp_path):
    ParkedStatus(str(tmp_path)).missing(("Erode", 135801), 3)
    ParkedStatus(str(tmp_path)).back(("Erode", 135801), 3)

    frame = p3.events_frame(p3.read_events(str(tmp_path)), {135801: "C32 Broadband"})

    assert list(frame["What happened"]) == [
        "Probe is back; parked units resume",
        "Probe went missing; its units are parked",
    ]
    assert set(frame["Network"]) == {"C32 Broadband"}
