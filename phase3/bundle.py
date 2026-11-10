"""Builds the summary the live page reads. The scan uploads it to the phase3-state release with each status update.

It reuses the readers in gui/phase3_data.py, which the local GUI also uses, so both show the same numbers.
Every table is turned into plain records here, so the live page only has to display them.
The failures section reads every result file, so it is built only when a run is pushed, not on each heartbeat.
"""

import collections
import datetime
import os

from gui import phase3_data as p3
from phase3 import config
from phase3.runs import latest_run_number, run_paths

CONSOLE_LOG = os.path.join(os.path.dirname(config.OUTPUT_DIR), "phase3_console.log")
EVENTS_SHOWN = 200
FAILURE_ROWS_SHOWN = 500
LOG_LINES_SHOWN = 200
COMMANDS = [
    ("Pull the latest scan state to this PC", "python -m phase3 sync pull"),
    ("Run the scan on this PC (pulls first, pushes when it stops)", "scripts/phase3-run-local.sh"),
    ("Show the saved state from the command line", "python -m phase3 sync status"),
    ("Start the scan on GitHub now", "Actions → phase3-scan → Run workflow"),
    ("Pause the scan on GitHub", "Actions → open the running phase3-scan run → Cancel workflow (it stops after its current unit and saves)"),
    ("Pause a scan on this PC", "python -m phase3 pause"),
    ("Failures report for the latest run", "python -m phase3 failures"),
]


def _tail(path, lines):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [line.rstrip("\n") for line in collections.deque(fh, maxlen=lines)]


def _records(frame):
    return frame.to_dict("records")


def _targets_section():
    targets = p3.targets_frame()
    cities = sorted(targets["City"].unique()) if not targets.empty else []
    return {
        "count": len(targets),
        "selection_summary": _records(p3.selection_summary_frame()),
        "tier_mix": _records(p3.tier_mix_frame(targets)),
        "cities": {city: _records(p3.city_targets_frame(targets, city)) for city in cities},
        "city_isp": {city: _records(p3.city_isp_counts_frame(targets, city)) for city in cities},
    }


def _run_section(number, manifest, networks):
    if not number:
        return None, {"overview": {}, "city_progress": [], "parked": None}
    paths = run_paths(config.OUTPUT_DIR, number)
    done = p3.read_done_units(paths["progress"])
    probe_cities = list(manifest.get("plan", {}).get("cities", {}))
    parked = p3.read_parked(paths["root"])
    events = p3.read_events(paths["root"], limit=EVENTS_SHOWN)
    section = {
        "overview": p3.run_status(number, done, paths["progress"]),
        "city_progress": _records(p3.city_progress_frame(done, manifest.get("target_count", 0), probe_cities)),
        "parked": {
            "updated_at": parked.get("updated_at"),
            "released_for_retry": parked.get("released_for_retry", 0),
            "gave_up": p3.gave_up_count(paths["root"]),
            "pairs": _records(p3.parked_pairs_frame(parked, networks)),
            "units": _records(p3.parked_units_frame(parked, p3.targets_frame())),
            "events": _records(p3.events_frame(events, networks)),
        },
    }
    return paths, section


def _failures_section(paths):
    frame = p3.failures_frame(paths["results"])
    by_status = frame["status"].value_counts().to_dict() if not frame.empty else {}
    return {
        "count": len(frame),
        "by_status": by_status,
        "rows": _records(frame.head(FAILURE_ROWS_SHOWN)),
    }


def build_bundle(status, with_failures):
    number = latest_run_number(config.OUTPUT_DIR)
    manifest = p3.read_manifest() or {}
    networks = p3.asn_network_lookup(manifest)
    paths, run = _run_section(number, manifest, networks)
    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "overview": run["overview"],
        "city_progress": run["city_progress"],
        "plan": _records(p3.probe_plan_frame(manifest)),
        "targets": _targets_section(),
        "parked": run["parked"],
        "runs": p3.list_runs(),
        "failures": _failures_section(paths) if (with_failures and paths) else None,
        "logs": {
            "progress": _tail(paths["progress"], LOG_LINES_SHOWN // 10) if paths else [],
            "events": _tail(os.path.join(paths["root"], p3.EVENTS_FILE), LOG_LINES_SHOWN // 10) if paths else [],
            "console": _tail(CONSOLE_LOG, LOG_LINES_SHOWN),
        },
        "commands": [{"what": what, "command": command} for what, command in COMMANDS],
    }
