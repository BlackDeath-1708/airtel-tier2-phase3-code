"""Pure data loading for the Phase 3 GUI page (gui/views/phase3_view.py).

Kept Streamlit-free so it can be unit-tested. Reads the Phase 3 run files and the
target selection; never writes anything except through `request_pause`.
"""
import collections
import datetime
import json
import os
import time

import pandas as pd

from phase3 import config
from phase3.checkpoint import split_unit_key, unit_output_path
from phase3.runs import failed_rows, is_success, latest_run_number, read_state, run_paths

STALE_AFTER_SEC = 300
SELECTION_DIR = os.path.dirname(config.TARGETS_CSV)
SELECTION_SUMMARY = os.path.join(SELECTION_DIR, "_selection_summary.csv")
SELECTION_LOG = os.path.join(SELECTION_DIR, "_selection_log.txt")


def read_manifest():
    if not os.path.exists(config.MANIFEST_FILE):
        return None
    with open(config.MANIFEST_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def list_runs():
    """Every run folder, newest first, with the status its state file last recorded."""
    number = latest_run_number(config.OUTPUT_DIR)
    rows = []
    for n in range(number, 0, -1):
        state = read_state(run_paths(config.OUTPUT_DIR, n)) or {}
        rows.append({
            "Run": f"run_{n:02d}",
            "Status (state file)": state.get("status", "unknown"),
            "Units total": state.get("units_total", 0),
        })
    return rows


def read_done_units(progress_path):
    """Unit keys marked done. A torn last line from a crash is skipped, as the runner does."""
    done = set()
    if not os.path.exists(progress_path):
        return done
    with open(progress_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") == "done":
                done.add(record["unit"])
    return done


def run_status(number, done_units, progress_path, now=None):
    """Overview block: units, traceroutes, and whether the progress file is still being written."""
    manifest = read_manifest() or {}
    cities = manifest.get("plan", {}).get("cities", {})
    targets = manifest.get("target_count", 0)
    state = read_state(run_paths(config.OUTPUT_DIR, number)) or {}
    age = None
    if os.path.exists(progress_path):
        age = (now if now is not None else time.time()) - os.path.getmtime(progress_path)
    return {
        "run": number,
        "state_file": state.get("status", "none"),
        "units_done": len(done_units),
        "units_total": targets * len(cities),
        "traceroutes_done": len(done_units) * config.TRACEROUTES_PER_IP_CITY,
        "traceroutes_total": manifest.get("traceroutes_total", 0),
        "target_count": targets,
        "progress_age_sec": round(age) if age is not None else None,
        "moving": age is not None and age < STALE_AFTER_SEC,
    }


def city_progress_frame(done_units, target_count, probe_cities):
    done_per_city = collections.Counter(split_unit_key(key)[1] for key in done_units)
    rows = []
    for city in sorted(probe_cities):
        done = done_per_city.get(city, 0)
        rows.append({
            "Probe_City": city,
            "Done": done,
            "Of": target_count,
            "Percent": round(done / target_count * 100, 1) if target_count else 0.0,
        })
    return pd.DataFrame(rows, columns=["Probe_City", "Done", "Of", "Percent"])


def rate_per_min(samples):
    """Units per minute from (timestamp, units_done) samples; 0 until there are two."""
    if len(samples) < 2 or samples[-1][0] <= samples[0][0]:
        return 0.0
    span = samples[-1][0] - samples[0][0]
    return (samples[-1][1] - samples[0][1]) / span * 60


def eta_seconds(done, total, rate):
    if rate <= 0:
        return None
    return max(total - done, 0) / (rate / 60)


def probe_plan_frame(manifest):
    rows = []
    for city, info in sorted(manifest.get("plan", {}).get("cities", {}).items()):
        for probe in info["probes"]:
            rows.append({
                "Probe_City": city,
                "Network": probe["network"],
                "ASN": probe["asn"],
                "Traceroutes_per_IP": probe["allocation"],
                "Tier": "Telecom" if probe["telecom"] else "Cloud / other",
            })
    return pd.DataFrame(rows, columns=["Probe_City", "Network", "ASN", "Traceroutes_per_IP", "Tier"])


def targets_frame():
    """The frozen target list: one row per target IP with its city, ISP and tier."""
    if not os.path.exists(config.TARGETS_CSV):
        return pd.DataFrame(columns=["Target IP", "City", "ISP", "Tier"])
    return pd.read_csv(config.TARGETS_CSV, dtype=str)


def selection_summary_frame():
    if not os.path.exists(SELECTION_SUMMARY):
        return pd.DataFrame()
    return pd.read_csv(SELECTION_SUMMARY)


def tier_mix_frame(targets):
    if targets.empty:
        return pd.DataFrame(columns=["City", "Tier", "IPs"])
    return targets.groupby(["City", "Tier"]).size().reset_index(name="IPs")


TIER_LABELS = {"telecom": "Telecom", "other": "Other network", "cloud": "Cloud / hosting"}


def tier_label(tier):
    return TIER_LABELS.get(tier, tier)


TIER_ORDER = {"telecom": 0, "other": 1, "cloud": 2}


def city_targets_frame(targets, city):
    """The target IPs of one city: telecom first, then other network, then cloud; grouped by ISP within each."""
    rows = targets[targets["City"] == city].copy()
    rows["_order"] = rows["Tier"].map(TIER_ORDER)
    rows = rows.sort_values(["_order", "ISP", "Target IP"])
    rows["Tier"] = rows["Tier"].map(tier_label)
    return rows[["Target IP", "ISP", "Tier"]].reset_index(drop=True)


def city_isp_counts_frame(targets, city):
    """How many target IPs each ISP contributes to one city, with its tier."""
    rows = targets[targets["City"] == city].copy()
    rows["Tier"] = rows["Tier"].map(tier_label)
    counts = rows.groupby(["ISP", "Tier"]).size().reset_index(name="IPs")
    return counts.sort_values("IPs", ascending=False).reset_index(drop=True)


def graph_target_catalog(targets, measured_ips):
    """Target IPs already measured from one probe city, with city, ISP, tier and a picker label for the graph."""
    cols = ["Target IP", "City", "ISP", "Tier", "Label"]
    if targets.empty or not measured_ips:
        return pd.DataFrame(columns=cols)
    rows = targets[targets["Target IP"].isin(measured_ips)].copy()
    rows["_order"] = rows["Tier"].map(TIER_ORDER)
    rows = rows.sort_values(["City", "_order", "ISP", "Target IP"])
    rows["Label"] = rows["Target IP"] + " · " + rows["ISP"] + " · " + rows["Tier"].map(tier_label)
    return rows[cols].reset_index(drop=True)


def read_selection_log():
    if not os.path.exists(SELECTION_LOG):
        return ""
    with open(SELECTION_LOG, encoding="utf-8") as fh:
        return fh.read()


def unit_record(results_root, city, ip):
    path = unit_output_path(results_root, ip, city)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _avg_rtt(hop):
    rtts = [t["rtt"] for t in hop.get("timings") or [] if t.get("rtt") is not None]
    return round(sum(rtts) / len(rtts), 2) if rtts else None


def _measurements(record):
    """(label, round, measurement) for each of the unit's traceroutes, numbered T1..Tn in order."""
    number = 0
    for round_record in record["rounds"]:
        for measurement in round_record["measurements"]:
            number += 1
            yield f"T{number}", round_record["round"], measurement


def asn_network_lookup(manifest):
    """{ASN: network name} from the frozen plan. Used when a result record has no network name for its probe."""
    lookup = {}
    for info in (manifest or {}).get("plan", {}).get("cities", {}).values():
        for probe in info["probes"]:
            lookup.setdefault(probe["asn"], probe["network"])
    return lookup


def iteration_label(label, round_no, measurement, networks=None):
    """Option text that names the probe's ISP, e.g. 'T3 · Bharti Airtel (AS24560) · round 2'."""
    probe = measurement.get("probe") or {}
    asn = probe.get("asn")
    network = probe.get("network") or (networks or {}).get(asn) or "unknown network"
    asn_text = f" (AS{asn})" if asn else ""
    return f"{label} · {network}{asn_text} · round {round_no}"


def traceroute_options(record, networks=None):
    """(label, option text) for each traceroute in the unit, in the order they were run."""
    return [
        (label, iteration_label(label, round_no, measurement, networks))
        for label, round_no, measurement in _measurements(record)
    ]


def traceroute_frame(record, networks=None):
    """One row per (traceroute, hop) for the graph and hop table. Silent hops keep a row, with address '*'."""
    rows = []
    for label, round_no, measurement in _measurements(record):
        probe = measurement.get("probe") or {}
        network = probe.get("network") or (networks or {}).get(probe.get("asn"))
        for hop_index, hop in enumerate(measurement.get("hops") or [], start=1):
            rows.append({
                "Traceroute": label,
                "Iteration": iteration_label(label, round_no, measurement, networks),
                "Round": round_no,
                "Probe_ASN": probe.get("asn"),
                "Probe_Network": network,
                "Hop": hop_index,
                "Hop_IP": hop.get("resolvedAddress") or "*",
                "Hop_Hostname": hop.get("resolvedHostname") or "",
                "Avg_RTT_ms": _avg_rtt(hop),
            })
    return pd.DataFrame(rows, columns=[
        "Traceroute", "Iteration", "Round", "Probe_ASN", "Probe_Network",
        "Hop", "Hop_IP", "Hop_Hostname", "Avg_RTT_ms",
    ])


def traceroute_summary_frame(record):
    rows = []
    for label, round_no, measurement in _measurements(record):
        probe = measurement.get("probe") or {}
        rows.append({
            "Traceroute": label,
            "Round": round_no,
            "Probe_ASN": probe.get("asn"),
            "Probe_Network": probe.get("network"),
            "Status": measurement.get("status", "unknown"),
            "Reached": is_success(measurement),
            "Hops": len(measurement.get("hops") or []),
        })
    return pd.DataFrame(rows)


def failures_frame(results_root):
    """Every failed traceroute in a run (the same rows `python -m phase3 failures` writes)."""
    return pd.DataFrame(failed_rows(results_root), columns=["ip", "probe_city", "asn", "round", "status", "hops"])


def scan_processes():
    """Running phase3 command lines (Linux /proc). Tokens come from the environment, so they never appear here."""
    found = []
    if not os.path.isdir("/proc"):
        return found
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(os.path.join("/proc", pid, "cmdline"), "rb") as fh:
                args = fh.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            continue
        if "-m phase3 " in args and "streamlit" not in args:
            found.append(f"pid {pid}: {args}")
    return found


def token_count():
    """How many tokens the scan will use. Only the count is returned; the values are never shown."""
    from phase3.__main__ import _tokens_from_env

    return len(_tokens_from_env())


def request_pause(pause_file):
    os.makedirs(os.path.dirname(pause_file), exist_ok=True)
    open(pause_file, "w", encoding="utf-8").close()


def pause_requested(pause_file):
    return os.path.exists(pause_file)


PARKED_FILE = "parked.json"
EVENTS_FILE = "events.jsonl"
EVENT_LABELS = {
    "missing": "Probe went missing; its units are parked",
    "recheck": "Still missing; checking again",
    "back": "Probe is back; parked units resume",
    "gave_up": "Gave up; units recorded as no_probe",
}


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30), "IST")


def to_ist(iso_utc):
    """Shows a UTC timestamp from the scan in IST, e.g. '2026-10-30 19:35:09 IST'. The files keep UTC."""
    if not iso_utc:
        return "n/a"
    return datetime.datetime.fromisoformat(iso_utc).astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def read_parked(run_root):
    """The parked-units snapshot the scan writes. Empty lists when nothing is parked or the scan has not reported yet."""
    path = os.path.join(run_root, PARKED_FILE)
    if not os.path.exists(path):
        return {"updated_at": None, "pairs": [], "units": []}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_events(run_root, limit=200):
    """Newest-first list of parked-unit events, skipping any torn line from a crash."""
    path = os.path.join(run_root, EVENTS_FILE)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(events[-limit:]))


def parked_pairs_frame(parked, networks):
    rows = [{
        "Probe city": pair["probe_city"],
        "Network": networks.get(pair["asn"], "unknown"),
        "ASN": pair["asn"],
        "Parked units": pair["parked_units"],
        "Released for retry": pair.get("released_for_retry", 0),
        "Rechecks": pair.get("rechecks", 0),
        "Missing since (IST)": to_ist(pair["missing_since"]),
        "Next check (IST)": to_ist(pair["next_check_at"]),
        "Gives up at (IST)": to_ist(pair["gives_up_at"]),
    } for pair in parked["pairs"]]
    return pd.DataFrame(rows, columns=[
        "Probe city", "Network", "ASN", "Parked units", "Released for retry", "Rechecks",
        "Missing since (IST)", "Next check (IST)", "Gives up at (IST)",
    ])


def parked_units_frame(parked, targets):
    """Every parked unit with its target city, ISP and the probe it is waiting for."""
    isp_of = dict(zip(targets["Target IP"], targets["ISP"])) if not targets.empty else {}
    rows = [{
        "Target IP": unit["ip"],
        "Target city": unit["city"],
        "ISP": isp_of.get(unit["ip"], "unknown"),
        "Waiting for probe city": unit["waiting_for_probe_city"],
        "Waiting for ASN": unit["waiting_for_asn"],
    } for unit in parked["units"]]
    return pd.DataFrame(rows, columns=[
        "Target IP", "Target city", "ISP", "Waiting for probe city", "Waiting for ASN",
    ])


def events_frame(events, networks):
    rows = [{
        "Time (IST)": to_ist(event["time"]),
        "What happened": EVENT_LABELS.get(event["event"], event["event"]),
        "Probe city": event["probe_city"],
        "ASN": event["asn"],
        "Network": networks.get(event["asn"], "unknown"),
        "Units": event["units"],
    } for event in events]
    return pd.DataFrame(rows, columns=["Time (IST)", "What happened", "Probe city", "ASN", "Network", "Units"])


def gave_up_count(run_root):
    return sum(1 for event in read_events(run_root, limit=10**9) if event["event"] == "gave_up")
