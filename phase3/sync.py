"""Shares the scan state through a private GitHub release, so the scan can run on this PC or in GitHub Actions.

The release "phase3-state" holds two assets:
  phase3-state.tar.gz  the whole output/phase3 folder (progress, results, manifest; no PAUSE file)
  status.json          a small summary anyone can read, which also holds the lock

Only one runner works at a time. A run writes running_on and a heartbeat into status.json, and another
runner refuses to start while that heartbeat is fresh. A heartbeat older than LOCK_STALE_SEC means the
earlier run died, so the lock is taken over.
"""

import datetime
import json
import os
import shutil
import subprocess
import tarfile
import tempfile

from phase3 import config
from phase3.bundle import build_bundle
from phase3.runs import latest_run_number, read_state, run_paths

RELEASE_TAG = "phase3-state"
STATE_ARCHIVE = "phase3-state.tar.gz"
STATUS_FILE = "status.json"
VIEW_FILE = "view.json"
TARGETS_NAME = "phase3_targets_selected.csv"  # the target list is kept as a release asset, not in git
HEARTBEAT_SEC = 10 * 60
CHECKPOINT_EVERY = 3  # the whole state is saved every third heartbeat (30 minutes)
LOCK_STALE_SEC = 45 * 60


def _gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _age_sec(iso_time):
    return (_now() - datetime.datetime.fromisoformat(iso_time)).total_seconds()


def _ensure_release():
    if subprocess.run(["gh", "release", "view", RELEASE_TAG], capture_output=True).returncode != 0:
        _gh("release", "create", RELEASE_TAG, "--title", "Phase 3 scan state",
            "--notes", "Managed by python -m phase3 sync. Do not edit by hand.")


def units_done(progress_path):
    """Distinct units finished in a progress log. A resumed run can write a unit's line twice."""
    if not os.path.exists(progress_path):
        return 0
    with open(progress_path, encoding="utf-8") as fh:
        return len({line.strip() for line in fh if line.strip()})


def current_status(owner, running_on):
    number = latest_run_number(config.OUTPUT_DIR)
    state = None
    progress = None
    if number:
        paths = run_paths(config.OUTPUT_DIR, number)
        state = read_state(paths)
        progress = paths["progress"]
    return {
        "updated_at": _now().isoformat(timespec="seconds"),
        "last_writer": owner,
        "run": number or None,
        "run_status": state["status"] if state else None,
        "units_done": units_done(progress) if progress else 0,
        "units_total": state["units_total"] if state else None,
        "running_on": running_on,
        "heartbeat": _now().isoformat(timespec="seconds") if running_on else None,
    }


def read_status():
    """The status summary from the release, or {} when there is none yet."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _gh("release", "download", RELEASE_TAG, "--pattern", STATUS_FILE, "--dir", tmp, "--clobber")
        except subprocess.CalledProcessError:
            return {}
        with open(os.path.join(tmp, STATUS_FILE), encoding="utf-8") as fh:
            return json.load(fh)


def _publish_status(status, with_failures=False):
    """Uploads the status summary and the live page's view of the scan."""
    _ensure_release()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, STATUS_FILE)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(status, fh, indent=2)
        view_path = os.path.join(tmp, VIEW_FILE)
        with open(view_path, "w", encoding="utf-8") as fh:
            json.dump(build_bundle(status, with_failures), fh)
        _gh("release", "upload", RELEASE_TAG, path, view_path, "--clobber")


def _pack(archive_path):
    def leave_out_pause_file(info):
        return None if info.name.endswith("/PAUSE") else info

    folder = config.OUTPUT_DIR
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(folder, arcname=os.path.basename(folder), filter=leave_out_pause_file)


def _unpack(archive_path):
    folder = config.OUTPUT_DIR
    shutil.rmtree(folder, ignore_errors=True)
    with tarfile.open(archive_path) as tar:
        tar.extractall(os.path.dirname(folder), filter="data")


def _fetch_targets():
    """Downloads the target list when this runner does not have it yet. A list already here is kept."""
    path = config.TARGETS_CSV
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        _gh("release", "download", RELEASE_TAG, "--pattern", TARGETS_NAME, "--dir", tmp, "--clobber")
        shutil.move(os.path.join(tmp, TARGETS_NAME), path)


def pull():
    """Replaces the local scan folder with the release copy.

    Refuses when this PC has more finished units than the release, because the pull would throw them away.
    """
    _fetch_targets()
    remote = read_status()
    remote_done = remote.get("units_done", 0)
    local_done = current_status(None, None)["units_done"]
    if local_done > remote_done:
        raise SystemExit(f"This PC has {local_done} finished units but the release has {remote_done}. "
                         "Run sync push from this PC first.")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _gh("release", "download", RELEASE_TAG, "--pattern", STATE_ARCHIVE, "--dir", tmp, "--clobber")
        except subprocess.CalledProcessError:
            print("No scan state in the release yet; starting fresh.")
            return
        _unpack(os.path.join(tmp, STATE_ARCHIVE))
    print(f"Pulled the scan state: {remote_done} units done.")


def _upload_archive():
    """Uploads the whole local scan folder, so the release always has the latest finished units."""
    _ensure_release()
    with tempfile.TemporaryDirectory() as tmp:
        archive = os.path.join(tmp, STATE_ARCHIVE)
        _pack(archive)
        _gh("release", "upload", RELEASE_TAG, archive, "--clobber")


def push(owner):
    """Uploads the local scan folder and its status, and clears the lock."""
    _upload_archive()
    _publish_status(current_status(owner, None), with_failures=True)
    print("Pushed the scan state.")


def lock(owner):
    """Takes the lock, or refuses while another runner's heartbeat is fresh."""
    remote = read_status()
    heartbeat = remote.get("heartbeat")
    if remote.get("running_on") and heartbeat and _age_sec(heartbeat) < LOCK_STALE_SEC:
        raise SystemExit(f"The scan is already running on {remote['running_on']} "
                         f"(last heartbeat {heartbeat}). Wait for it to finish.")
    _publish_status(current_status(owner, owner))


def heartbeat_loop(owner, stop_event):
    """Keeps the lock fresh and saves the state while a long run is going. Stops when stop_event is set.

    A run that is cancelled or hits its time limit never reaches the final push, so the state is also
    saved here every CHECKPOINT_EVERY beats. A failed save is reported and retried on the next beat.
    """
    beats = 0
    while not stop_event.wait(HEARTBEAT_SEC):
        beats += 1
        if beats % CHECKPOINT_EVERY == 0:
            try:
                _upload_archive()
            except (subprocess.CalledProcessError, OSError) as error:
                print(f"Could not save the state just now; retrying on the next checkpoint: {error}", flush=True)
        try:
            _publish_status(current_status(owner, owner))
        except (subprocess.CalledProcessError, OSError) as error:
            print(f"Could not update the status just now; retrying on the next heartbeat: {error}", flush=True)
