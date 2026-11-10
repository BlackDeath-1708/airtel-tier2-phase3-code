"""Runs work items (IP, probe city) on a pool of workers, one result file per item.

Pause and failures stop new items from starting. Items already running finish and are logged,
so the progress log always matches the result files on disk.

A unit whose probe is not connected is parked until that probe's cooldown ends, instead of being
recorded as finished. If the probe is still missing after PROBE_GIVE_UP_AFTER_SEC, the unit is
recorded with no_probe for that probe and the run moves on.
"""

import datetime
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from phase3 import config
from phase3.checkpoint import (
    PauseRequested,
    append_progress,
    raise_if_paused,
    unit_key,
    unit_output_path,
    write_json_atomic,
)
from phase3.globalping import CreditsExhausted, ProbeUnavailable, RequestFailed, no_probe_result
from phase3.parked import ParkedStatus

QUEUE_PER_WORKER = 2
NO_FORCED = frozenset()
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _ist_time_at(monotonic_deadline):
    """Wall-clock IST time for a monotonic deadline, for the log."""
    wall = time.time() + (monotonic_deadline - time.monotonic())
    return datetime.datetime.fromtimestamp(wall, IST).strftime("%H:%M:%S IST")


def _measure(client, ip, selector, forced):
    if (selector["city"], selector["asn"]) in forced:
        measurement = no_probe_result(selector)
    else:
        measurement = client.traceroute(ip, selector)
    return dict(measurement, selector=dict(selector))


def run_unit(client, item, forced=NO_FORCED):
    """Runs the traceroutes of one work item; each measurement keeps the selector it was made from.

    The traceroutes of one round go to different probes, so they run at the same time. Rounds run in order.
    Probe pairs in `forced` are recorded as no_probe without asking Globalping (the run gave up on them).
    """
    results = []
    for round_record in item["rounds"]:
        selectors = round_record["selectors"]
        with ThreadPoolExecutor(max_workers=max(1, len(selectors))) as pool:
            measurements = list(pool.map(lambda selector: _measure(client, item["ip"], selector, forced), selectors))
        results.append({"round": round_record["round"], "measurements": measurements})
    return results


def _run_and_store(client, item, results_root, pause_file, stop_flag, forced=NO_FORCED):
    """Returns the unit key once stored, or None if a pause was requested before this unit started."""
    try:
        raise_if_paused(pause_file, stop_flag)
    except PauseRequested:
        return None
    record = {"ip": item["ip"], "city": item["city"], "rounds": run_unit(client, item, forced)}
    write_json_atomic(unit_output_path(results_root, item["ip"], item["city"]), record)
    return unit_key(item["ip"], item["city"])


def _release_ready(waiting, queue, forced, client, now, parked=None):
    """Moves parked units back to the queue once their probe pair's cooldown has ended.

    A pair that has been missing for PROBE_GIVE_UP_AFTER_SEC is given up: its units run with that
    pair recorded as no_probe, so the run can finish.
    """
    for key in list(waiting):
        state = client.unavailable(key)
        give_up = state is not None and now - state["since"] >= config.PROBE_GIVE_UP_AFTER_SEC
        still_cooling = state is not None and now < state["retry_at"]
        if still_cooling and not give_up:
            continue
        units = waiting.pop(key)
        if parked is not None:
            if give_up:
                parked.gave_up(key, len(units))
            elif state is None:
                parked.back(key, len(units))
            else:
                parked.recheck(key, len(units))
            parked.released(key, [unit_key(item["ip"], item["city"]) for item in units])
        for item in units:
            if give_up:
                forced.setdefault(unit_key(item["ip"], item["city"]), set()).add(key)
            queue.append(item)


def _forced_for(forced, item):
    return frozenset(forced.get(unit_key(item["ip"], item["city"]), ()))


def run_items(items, client, results_root, progress_path, skip, pause_file, stop_flag,
              workers=1, on_progress=None, parked_dir=None):
    """Returns (status, units_done) where status is 'complete', 'paused' or 'failed'.

    on_progress(units_done, units_total) is called after each finished unit.
    parked_dir, when given, receives parked.json and events.jsonl so the GUI can show parked units.
    """
    parked = ParkedStatus(parked_dir) if parked_dir else None
    items_by_key = {unit_key(item["ip"], item["city"]): item for item in items}
    waiting = parked.restore(client, items_by_key) if parked is not None else {}  # probe pair -> parked units
    restored = {unit_key(item["ip"], item["city"]) for units in waiting.values() for item in units}
    pending = [item for item in items
               if unit_key(item["ip"], item["city"]) not in skip and unit_key(item["ip"], item["city"]) not in restored]
    total = len(pending)
    queue = deque(pending)
    forced = {}  # unit key -> probe pairs to record as no_probe
    status = "complete"
    units_done = 0
    consecutive_failures = 0
    credits_wait_until = 0.0  # monotonic time before which no new measurement is started (credits are out)
    running = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            _release_ready(waiting, queue, forced, client, time.monotonic(), parked)
            if parked is not None:
                parked.snapshot(waiting, client)
            while (status == "complete" and len(running) < workers * QUEUE_PER_WORKER
                   and time.monotonic() >= credits_wait_until):
                try:
                    raise_if_paused(pause_file, stop_flag)
                except PauseRequested:
                    status = "paused"
                    break
                if not queue:
                    break
                item = queue.popleft()
                future = pool.submit(
                    _run_and_store, client, item, results_root, pause_file, stop_flag, _forced_for(forced, item),
                )
                running[future] = item
            if not running:
                if status == "complete" and (waiting or queue):
                    # Only parked units, or units held back until credits return: wait, but stay pausable.
                    try:
                        raise_if_paused(pause_file, stop_flag)
                    except PauseRequested:
                        status = "paused"
                        break
                    time.sleep(config.PROBE_WAIT_POLL_SEC)
                    continue
                break
            finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
            for future in finished:
                item = running.pop(future)
                try:
                    key = future.result()
                except RequestFailed as error:
                    # Put the unit back at the end of the queue and keep the run going.
                    print(f"\nRetrying {item['ip']} / {item['city']} later: {error}")
                    queue.append(item)
                    consecutive_failures += 1
                    if consecutive_failures >= config.CONSECUTIVE_FAILURE_LIMIT and status == "complete":
                        print(f"\nStopped: {consecutive_failures} measurements in a row did not finish. "
                              "Globalping may be stalled. Resume once it answers: python -m phase3 resume")
                        status = "paused"
                    continue
                except CreditsExhausted as error:
                    if not config.CREDITS_AUTO_RESUME:
                        if status == "complete":
                            print(f"\nStopped: {error}. Top up the Globalping credits, then resume: python -m phase3 resume")
                        status = "paused"
                        continue
                    # Keep the unit and wait for the first token to recover, then carry on by itself.
                    queue.appendleft(item)
                    if error.retry_at > credits_wait_until:
                        credits_wait_until = error.retry_at
                        print(f"\nGlobalping credits are out on every token. Trying again automatically at "
                              f"{_ist_time_at(error.retry_at)}.")
                    continue
                except ProbeUnavailable as error:
                    consecutive_failures = 0
                    waiting.setdefault(error.key, deque()).append(item)
                    if parked is not None:
                        parked.settled(unit_key(item["ip"], item["city"]))
                        parked.missing(error.key, len(waiting[error.key]))
                    continue
                consecutive_failures = 0
                if key is None:
                    status = "paused" if status == "complete" else status
                    continue
                if parked is not None:
                    parked.settled(key)
                append_progress(progress_path, key)
                units_done += 1
                if on_progress is not None:
                    on_progress(units_done, total)
    if parked is not None:
        parked.snapshot(waiting, client, force=True)
    return status, units_done
