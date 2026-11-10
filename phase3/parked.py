"""Visible record of parked units, for the GUI: which probes are missing, what is waiting, and what happened.

Writes two files in the run folder:
  parked.json   the current snapshot (rewritten at most every SNAPSHOT_EVERY_SEC, and at the end of a session)
  events.jsonl  one line per change: missing, recheck, back, gave_up
"""

import collections
import datetime
import json
import os
import time

from phase3 import config
from phase3.checkpoint import unit_key, write_json_atomic

PARKED_FILE = "parked.json"
EVENTS_FILE = "events.jsonl"
STATE_FILE = "parked_state.json"  # what a restart needs to pick up the parked units again
SNAPSHOT_EVERY_SEC = 10


def _iso(wall_seconds):
    return datetime.datetime.fromtimestamp(wall_seconds, datetime.timezone.utc).isoformat()


def _wall_time_of(monotonic_deadline, now_monotonic):
    """Converts a monotonic deadline into wall-clock seconds, so another process can read it."""
    if monotonic_deadline == float("inf"):
        return None
    return time.time() + (monotonic_deadline - now_monotonic)


class ParkedStatus:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self._missing_since = {}  # probe pair -> wall time it first went missing
        self._rechecks = {}  # probe pair -> how many times it was checked again
        self._retry = {}  # unit key -> probe pair, for units handed back to the queue and not yet settled
        self._last_snapshot = 0.0

    def _append_event(self, kind, key, units):
        record = {
            "time": _iso(time.time()),
            "event": kind,
            "probe_city": key[0],
            "asn": key[1],
            "units": units,
        }
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.run_dir, EVENTS_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

    def missing(self, key, units):
        if key not in self._missing_since:
            self._missing_since[key] = time.time()
            self._append_event("missing", key, units)

    def recheck(self, key, units):
        self._rechecks[key] = self._rechecks.get(key, 0) + 1
        self._append_event("recheck", key, units)

    def released(self, key, unit_keys):
        """Units leave the parked list and go back to the queue to be tried again."""
        for unit_key in unit_keys:
            self._retry[unit_key] = key

    def settled(self, unit_key):
        """A unit was finished, or parked again, so it no longer waits for a retry."""
        self._retry.pop(unit_key, None)

    def back(self, key, units):
        self._missing_since.pop(key, None)
        self._append_event("back", key, units)

    def gave_up(self, key, units):
        self._missing_since.pop(key, None)
        self._append_event("gave_up", key, units)

    def snapshot(self, waiting, client, force=False):
        now = time.monotonic()
        if not force and now - self._last_snapshot < SNAPSHOT_EVERY_SEC:
            return
        self._last_snapshot = now
        released = collections.Counter(self._retry.values())
        pairs, units = [], []
        for key in _pair_keys(waiting, released):
            parked = waiting.get(key, ())
            state = client.unavailable(key) or {}
            since = self._missing_since.get(key)
            next_check = _wall_time_of(state["retry_at"], now) if state else None
            pairs.append({
                "probe_city": key[0],
                "asn": key[1],
                "parked_units": len(parked),
                "released_for_retry": released.get(key, 0),
                "rechecks": self._rechecks.get(key, 0),
                "missing_since": _iso(since) if since is not None else None,
                "next_check_at": _iso(next_check) if next_check is not None else None,
                "gives_up_at": _iso(since + config.PROBE_GIVE_UP_AFTER_SEC) if since is not None else None,
            })
            for item in parked:
                units.append({
                    "ip": item["ip"],
                    "city": item["city"],
                    "waiting_for_probe_city": key[0],
                    "waiting_for_asn": key[1],
                })
        write_json_atomic(os.path.join(self.run_dir, PARKED_FILE), {
            "updated_at": _iso(time.time()),
            "released_for_retry": sum(released.values()),
            "pairs": pairs,
            "units": units,
        })
        self._save_state(waiting, client, now)

    def _save_state(self, waiting, client, now):
        """What a restart needs: each pair's missing-since time, recheck count, cooldown end and parked unit keys."""
        pairs = []
        for key, parked in waiting.items():
            state = client.unavailable(key) or {}
            retry_at = _wall_time_of(state["retry_at"], now) if state else None
            pairs.append({
                "probe_city": key[0],
                "asn": key[1],
                "missing_since": self._missing_since.get(key),
                "rechecks": self._rechecks.get(key, 0),
                "retry_at": retry_at,
                "units": [unit_key(item["ip"], item["city"]) for item in parked],
            })
        write_json_atomic(os.path.join(self.run_dir, STATE_FILE), {"pairs": pairs})

    def restore(self, client, items_by_key):
        """Reads the saved state after a restart. Returns the parked units per pair, taken from the work items."""
        path = os.path.join(self.run_dir, STATE_FILE)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        now_wall, now_mono = time.time(), time.monotonic()
        waiting = {}
        for pair in saved["pairs"]:
            key = (pair["probe_city"], pair["asn"])
            units = collections.deque(items_by_key[uk] for uk in pair["units"] if uk in items_by_key)
            if not units:
                continue
            waiting[key] = units
            missing_since = pair["missing_since"]
            if missing_since is not None:
                self._missing_since[key] = missing_since
            self._rechecks[key] = pair["rechecks"]
            if pair["retry_at"] is not None:
                since = now_mono - (now_wall - missing_since) if missing_since is not None else now_mono
                client.restore_unavailable(key, since, now_mono + (pair["retry_at"] - now_wall))
        return waiting


def _pair_keys(waiting, released):
    """Every probe pair with parked or released units, in first-seen order."""
    return list(dict.fromkeys([*waiting, *released]))
