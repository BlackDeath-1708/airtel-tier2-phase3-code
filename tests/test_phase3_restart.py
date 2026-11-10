import collections
import time

from phase3 import config
from phase3.checkpoint import unit_key
from phase3.globalping import RequestFailed
from phase3.parked import ParkedStatus
from phase3.runner import run_items
from tests.test_phase3_probe_wait import PairMissingClient, _mumbai_items

PAIR = ("Mumbai", 31898)


class RecordingClient(PairMissingClient):
    """Remembers every traceroute attempt, and the cooldowns handed back after a restart."""

    def __init__(self, missing_key, back_at):
        super().__init__(missing_key, back_at)
        self.attempts = []
        self.restored = []

    def traceroute(self, target, location):
        self.attempts.append((target, (location["city"], location["asn"])))
        return super().traceroute(target, location)

    def restore_unavailable(self, key, since, retry_at):
        self.restored.append((key, retry_at))


class AlwaysFailing:
    """Every measurement times out on Globalping's side."""

    def unavailable(self, key):
        return None

    def traceroute(self, target, location):
        raise RequestFailed(f"measurement for {target} did not finish")


def _save_parked(run_dir, items, client):
    ParkedStatus(run_dir).snapshot({PAIR: collections.deque(items)}, client, force=True)


def test_parked_units_and_their_cooldown_are_restored_after_a_restart(tmp_path):
    items = _mumbai_items()
    parked_units = items[:2]
    before = RecordingClient(PAIR, back_at=time.monotonic() + 600)
    _save_parked(str(tmp_path), parked_units, before)

    after = RecordingClient(PAIR, back_at=time.monotonic() + 600)
    items_by_key = {unit_key(item["ip"], item["city"]): item for item in items}
    restored = ParkedStatus(str(tmp_path)).restore(after, items_by_key)

    assert [unit_key(item["ip"], item["city"]) for item in restored[PAIR]] == \
        [unit_key(item["ip"], item["city"]) for item in parked_units]
    assert [key for key, _ in after.restored] == [PAIR]
    assert after.restored[0][1] > time.monotonic()  # the cooldown is still running


def test_restored_units_are_not_run_a_second_time_from_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROBE_WAIT_POLL_SEC", 0.01)
    items = _mumbai_items()
    parked_units = items[:2]
    _save_parked(str(tmp_path), parked_units, RecordingClient(PAIR, back_at=time.monotonic() + 600))

    after = RecordingClient(PAIR, back_at=time.monotonic() + 600)
    deadline = time.monotonic() + 0.5
    status, _ = run_items(
        items,
        client=after,
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: time.monotonic() > deadline,
        workers=2,
        parked_dir=str(tmp_path),
    )

    parked_ips = {item["ip"] for item in parked_units}
    tried_while_parked = [target for target, key in after.attempts if key == PAIR and target in parked_ips]
    assert status == "paused"
    assert tried_while_parked == []


def test_run_pauses_after_too_many_measurements_in_a_row_do_not_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONSECUTIVE_FAILURE_LIMIT", 3)

    status, done = run_items(
        _mumbai_items(),
        client=AlwaysFailing(),
        results_root=str(tmp_path / "results"),
        progress_path=str(tmp_path / "progress.jsonl"),
        skip=set(),
        pause_file=str(tmp_path / "PAUSE"),
        stop_flag=lambda: False,
        workers=2,
    )

    assert status == "paused"
    assert done == 0
