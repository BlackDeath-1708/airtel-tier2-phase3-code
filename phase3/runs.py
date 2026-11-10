"""Run folders, run state, and the carry-forward of failed traceroutes into the next run."""

import json
import os

from phase3.checkpoint import write_json_atomic


def run_paths(output_dir, number):
    root = os.path.join(output_dir, f"run_{number:02d}")
    return {
        "root": root,
        "results": os.path.join(root, "results"),
        "progress": os.path.join(root, "progress.jsonl"),
        "state": os.path.join(root, "state.json"),
    }


def latest_run_number(output_dir):
    if not os.path.isdir(output_dir):
        return 0
    numbers = [
        int(name[len("run_"):])
        for name in os.listdir(output_dir)
        if name.startswith("run_") and name[len("run_"):].isdigit()
    ]
    return max(numbers, default=0)


def write_state(paths, status, units_total):
    write_json_atomic(paths["state"], {"status": status, "units_total": units_total})


def read_state(paths):
    if not os.path.exists(paths["state"]):
        return None
    with open(paths["state"], encoding="utf-8") as fh:
        return json.load(fh)


def is_success(measurement):
    return measurement.get("status") == "finished" and bool(measurement.get("hops"))


def _selector_of(measurement):
    if measurement.get("selector"):
        return measurement["selector"]
    # Records written before selectors were stored: the probe that answered is the best identity left.
    probe = measurement.get("probe") or {}
    return {"city": probe.get("city"), "asn": probe.get("asn")}


def _unit_files(results_root):
    if not os.path.isdir(results_root):
        return []
    found = []
    for folder, _, files in os.walk(results_root):
        found.extend(os.path.join(folder, name) for name in files if name.endswith(".json"))
    return sorted(found)


def failed_rows(results_root):
    """One row per traceroute that did not succeed, for the failures report."""
    rows = []
    for path in _unit_files(results_root):
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        for round_record in record["rounds"]:
            for measurement in round_record["measurements"]:
                if is_success(measurement):
                    continue
                selector = _selector_of(measurement)
                rows.append({
                    "ip": record["ip"],
                    "probe_city": record["city"],
                    "asn": selector.get("asn"),
                    "round": round_record["round"],
                    "status": measurement.get("status", "unknown"),
                    "hops": len(measurement.get("hops") or []),
                })
    return rows


def failed_items(results_root):
    """Work items for the next run: only the traceroutes that did not succeed, grouped by unit and round."""
    items = []
    for path in _unit_files(results_root):
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        rounds = []
        for round_record in record["rounds"]:
            selectors = [
                _selector_of(m) for m in round_record["measurements"] if not is_success(m)
            ]
            if selectors:
                rounds.append({"round": round_record["round"], "selectors": selectors})
        if rounds:
            items.append({"ip": record["ip"], "city": record["city"], "rounds": rounds})
    return items
