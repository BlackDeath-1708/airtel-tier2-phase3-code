"""Phase 3 command line.

  python -m phase3 plan    build and freeze the probe plan (no measurements)
  python -m phase3 start   freeze the plan and run 1 (refuses if a run already exists)
  python -m phase3 resume  continue the latest run from its progress log
  python -m phase3 next    after a complete run, start the next run with only its failed traceroutes
  python -m phase3 pause   ask a running process to stop after its current unit
  python -m phase3 status  show the latest run and its progress
"""

import argparse
import collections
import csv
import datetime
import json
import os
import signal
import sys
import threading
import time

from phase3 import config
from phase3.checkpoint import completed_units_to_skip, load_manifest, save_manifest
from phase3.globalping import GlobalpingClient, fetch_india_probes
from phase3.plan import build_plan, first_run_items, total_traceroutes
from phase3.runner import run_items
from phase3 import notify, sync
from phase3.runs import failed_items, failed_rows, latest_run_number, read_state, run_paths, write_state
from phase3.targets import load_targets, sha256_file


def _tokens_from_env():
    """The Globalping tokens from the GLOBALPING_TOKENS environment variable. Never printed."""
    raw = os.environ.get(config.TOKEN_ENV_VAR, "")
    return [token.strip() for token in raw.split(",") if token.strip()]


def _only_cities(raw):
    return [city.strip() for city in raw.split(",") if city.strip()] if raw else None


def _new_manifest(limit_ips, only_cities):
    targets = load_targets(config.TARGETS_CSV, limit=limit_ips)
    plan = build_plan(fetch_india_probes())
    if only_cities:
        plan["cities"] = {city: info for city, info in plan["cities"].items() if city in only_cities}
    return {
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "targets_csv": config.TARGETS_CSV,
        "targets_sha256": sha256_file(config.TARGETS_CSV),
        "limit_ips": limit_ips,
        "target_count": len(targets),
        "traceroutes_total": total_traceroutes(plan, len(targets)),
        "plan": plan,
    }


def _install_stop_handler():
    flag = {"stop": False}

    def handler(signum, frame):
        flag["stop"] = True
        print("\nStop requested: finishing the current unit, then pausing.")

    # SIGTERM is what a cancelled GitHub Actions run sends, so it stops the scan the same way Ctrl-C does.
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    return lambda: flag["stop"]


def _work_items_for_run(number, manifest):
    """Run 1 covers every target in every planned city. Run N+1 covers only run N's failed traceroutes."""
    if number == 1:
        targets = load_targets(config.TARGETS_CSV, limit=manifest["limit_ips"])
        return first_run_items(manifest["plan"], targets)
    return failed_items(run_paths(config.OUTPUT_DIR, number - 1)["results"])


def _hms(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m{secs:02d}s"


def _progress_printer():
    """Returns a callback that redraws one progress line: units done, rate, and estimated time left."""
    started = time.monotonic()

    def show(done, total):
        elapsed = max(time.monotonic() - started, 1e-6)
        rate_per_sec = done / elapsed
        remaining = (total - done) / rate_per_sec if done else 0
        print(
            f"\r{done:,}/{total:,} units ({done / total:.1%})  {rate_per_sec * 60:.1f}/min  ETA {_hms(remaining)}   ",
            end="",
            flush=True,
        )

    return show


def _execute_run(number, manifest, workers):
    paths = run_paths(config.OUTPUT_DIR, number)
    items = _work_items_for_run(number, manifest)
    skip = completed_units_to_skip(paths["progress"], config.SAFETY_WINDOW_UNITS, paths["results"])
    tokens = _tokens_from_env()
    if not tokens:
        sys.exit(f"Set {config.TOKEN_ENV_VAR} to a comma-separated list of Globalping tokens.")

    write_state(paths, "running", len(items))
    print(f"Run {number:02d}: {len(tokens)} tokens, {workers} workers. Pause with `python -m phase3 pause`.")
    try:
        status, done = run_items(
            items,
            client=GlobalpingClient(tokens),
            results_root=paths["results"],
            progress_path=paths["progress"],
            skip=skip,
            pause_file=config.PAUSE_FILE,
            stop_flag=_install_stop_handler(),
            workers=workers,
            on_progress=_progress_printer(),
            parked_dir=paths["root"],
        )
    except Exception:
        write_state(paths, "crashed", len(items))
        raise
    write_state(paths, status, len(items))
    print(f"\nRun {number:02d} {status}: {done} units completed in this session.")
    if status == "paused":
        print("Resume with: python -m phase3 resume")


def _check_targets_unchanged(manifest):
    if sha256_file(config.TARGETS_CSV) != manifest["targets_sha256"]:
        sys.exit("The targets file changed since the run started. Refusing to continue.")


def cmd_plan(args):
    manifest = _new_manifest(args.limit_ips, _only_cities(args.cities))
    save_manifest(config.MANIFEST_FILE, manifest)
    _print_summary(manifest)


def cmd_start(args):
    if latest_run_number(config.OUTPUT_DIR) > 0:
        sys.exit("A Phase 3 run already exists. Use `resume`, or delete the output folder to start over.")
    manifest = _new_manifest(args.limit_ips, _only_cities(args.cities))
    save_manifest(config.MANIFEST_FILE, manifest)
    _print_summary(manifest)
    _execute_run(1, manifest, args.workers)


def cmd_resume(args):
    number = latest_run_number(config.OUTPUT_DIR)
    if number == 0:
        sys.exit("No run found. Run `start` first.")
    state = read_state(run_paths(config.OUTPUT_DIR, number))
    if state and state["status"] == "complete":
        sys.exit(f"Run {number:02d} is already complete. Use `next` to carry its failures forward.")
    manifest = load_manifest(config.MANIFEST_FILE)
    _check_targets_unchanged(manifest)
    if os.path.exists(config.PAUSE_FILE):
        os.remove(config.PAUSE_FILE)
    _execute_run(number, manifest, args.workers)


def cmd_next(args):
    number = latest_run_number(config.OUTPUT_DIR)
    if number == 0:
        sys.exit("No run found. Run `start` first.")
    state = read_state(run_paths(config.OUTPUT_DIR, number))
    if not state or state["status"] != "complete":
        sys.exit(f"Run {number:02d} is not complete. Use `resume` first.")
    manifest = load_manifest(config.MANIFEST_FILE)
    _check_targets_unchanged(manifest)
    if not failed_items(run_paths(config.OUTPUT_DIR, number)["results"]):
        print(f"Nothing to carry forward: every traceroute in run {number:02d} succeeded.")
        return
    if os.path.exists(config.PAUSE_FILE):
        os.remove(config.PAUSE_FILE)
    _execute_run(number + 1, manifest, args.workers)


def cmd_pause(_args):
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    open(config.PAUSE_FILE, "w", encoding="utf-8").close()
    print("Pause requested. The run stops after its current unit.")


def cmd_status(_args):
    number = latest_run_number(config.OUTPUT_DIR)
    if number == 0:
        sys.exit("No run found.")
    paths = run_paths(config.OUTPUT_DIR, number)
    state = read_state(paths) or {"status": "unknown", "units_total": 0}
    done = len(completed_units_to_skip(paths["progress"], safety_window=0, results_root=paths["results"]))
    print(f"Run {number:02d}: {state['status']}  units done {done} of {state['units_total']}")


def _run_state(number):
    state = read_state(run_paths(config.OUTPUT_DIR, number))
    return state["status"] if state else "none"


def cmd_supervise(args):
    """Runs the scan unattended: starts or resumes, and restarts after any crash, until complete or paused."""
    if os.path.exists(config.PAUSE_FILE):
        os.remove(config.PAUSE_FILE)  # starting explicitly clears an old pause request
    delay = config.SUPERVISOR_BACKOFF_SEC
    while True:
        number = latest_run_number(config.OUTPUT_DIR)
        if number == 0:
            manifest = _new_manifest(args.limit_ips, _only_cities(args.cities))
            save_manifest(config.MANIFEST_FILE, manifest)
            number = 1
        else:
            manifest = load_manifest(config.MANIFEST_FILE)
            _check_targets_unchanged(manifest)
        if _run_state(number) == "complete":
            print(f"Run {number:02d} is complete.")
            return
        try:
            _execute_run(number, manifest, args.workers)
        except Exception as error:  # a crash must not end the unattended scan
            print(f"\nScan stopped with an error: {error!r}. Restarting in {delay}s.")
            time.sleep(delay)
            delay = min(delay * 2, config.SUPERVISOR_MAX_BACKOFF_SEC)
            continue
        status = _run_state(number)
        if status in ("complete", "paused"):
            print(f"Run {number:02d} {status}.")
            return
        print(f"Run {number:02d} ended with status '{status}'. Restarting in {delay}s.")
        time.sleep(delay)
        delay = min(delay * 2, config.SUPERVISOR_MAX_BACKOFF_SEC)


def cmd_sync(args):
    """Shares the scan state with GitHub. `run` pulls, locks, scans, then pushes, even when the scan stops early."""
    if args.action == "status":
        print(json.dumps(sync.read_status(), indent=2))
    elif args.action == "pull":
        sync.pull()
    elif args.action == "push":
        sync.push(args.owner)
    else:
        sync.pull()
        sync.lock(args.owner)
        stop_heartbeat = threading.Event()
        heartbeat = threading.Thread(target=sync.heartbeat_loop, args=(args.owner, stop_heartbeat, lambda: notify.post_progress(args.owner)), daemon=True)
        heartbeat.start()
        try:
            cmd_supervise(argparse.Namespace(workers=args.workers, limit_ips=None, cities=None))
        finally:
            stop_heartbeat.set()
            heartbeat.join()
            sync.push(args.owner)


FAILURE_FIELDS = ["ip", "probe_city", "asn", "round", "status", "hops"]


def cmd_failures(args):
    """Writes run_NN/failures.csv with one row per failed traceroute, and prints a status summary."""
    number = args.run or latest_run_number(config.OUTPUT_DIR)
    if number == 0:
        sys.exit("No run found.")
    paths = run_paths(config.OUTPUT_DIR, number)
    rows = failed_rows(paths["results"])
    report = os.path.join(paths["root"], "failures.csv")
    tmp = report + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FAILURE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, report)

    units_saved = len(completed_units_to_skip(paths["progress"], safety_window=0, results_root=paths["results"]))
    print(f"Run {number:02d}: {units_saved} units saved, {len(rows)} failed traceroutes")
    for status, count in collections.Counter(row["status"] for row in rows).most_common():
        print(f"  {status}: {count}")
    print(f"Report: {report}")


def _print_summary(manifest):
    plan = manifest["plan"]
    print(f"Targets: {manifest['target_count']}  Probe cities: {len(plan['cities'])}")
    for city, info in plan["cities"].items():
        probes = ", ".join(f"AS{p['asn']} {p['network']} x{p['allocation']}" for p in info["probes"])
        print(f"  {city}: {probes}")
    print(f"Total traceroutes: {manifest['traceroutes_total']:,}")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="phase3")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in [("plan", cmd_plan), ("start", cmd_start)]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--limit-ips", type=int, default=None, help="use only the first N target IPs (pilot)")
        cmd.add_argument("--cities", default=None, help="comma-separated probe cities to keep (pilot)")
        cmd.set_defaults(func=handler)
    for name, handler in [("resume", cmd_resume), ("next", cmd_next)]:
        sub.add_parser(name).set_defaults(func=handler)
    for name in ("start", "resume", "next"):
        sub.choices[name].add_argument("--workers", type=int, default=config.CONCURRENCY, help="parallel units")
    sub.add_parser("pause").set_defaults(func=cmd_pause)
    sub.add_parser("status").set_defaults(func=cmd_status)
    supervise = sub.add_parser("supervise")
    supervise.add_argument("--workers", type=int, default=config.CONCURRENCY, help="parallel units")
    supervise.add_argument("--limit-ips", type=int, default=None, help="use only the first N target IPs (pilot)")
    supervise.add_argument("--cities", default=None, help="comma-separated probe cities to keep (pilot)")
    supervise.set_defaults(func=cmd_supervise)
    sync_cmd = sub.add_parser("sync", help="share the scan state with the private GitHub release")
    sync_actions = sync_cmd.add_subparsers(dest="action", required=True)
    for name in ("status", "pull"):
        sync_actions.add_parser(name)
    for name in ("push", "run"):
        action = sync_actions.add_parser(name)
        action.add_argument("--owner", default="local", help="who is running: local or github")
        action.add_argument("--workers", type=int, default=config.CONCURRENCY, help="parallel units (run only)")
    sync_cmd.set_defaults(func=cmd_sync)
    failures = sub.add_parser("failures")
    failures.add_argument("--run", type=int, default=None, help="run number (default: latest)")
    failures.set_defaults(func=cmd_failures)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
