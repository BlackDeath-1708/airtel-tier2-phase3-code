"""Progress log, safety-window resume and pause handling for the Phase 3 run."""

import json
import os


class PauseRequested(Exception):
    """Raised between units when a pause file or a stop request is seen."""


def unit_key(ip, city):
    return f"{ip}|{city}"


def split_unit_key(key):
    ip, city = key.split("|", 1)
    return ip, city


def unit_output_path(results_root, ip, city):
    return os.path.join(results_root, city.lower().replace(" ", "_"), f"{ip}.json")


def write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp_path, path)


def append_progress(progress_path, key):
    os.makedirs(os.path.dirname(progress_path), exist_ok=True)
    needs_newline = _ends_without_newline(progress_path)
    with open(progress_path, "a", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")  # a crash left a torn last line; keep the new record on its own line
        fh.write(json.dumps({"unit": key, "status": "done"}) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _ends_without_newline(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    with open(path, "rb") as fh:
        fh.seek(-1, os.SEEK_END)
        return fh.read(1) != b"\n"


def _read_done_keys(progress_path):
    if not os.path.exists(progress_path):
        return []
    keys = []
    with open(progress_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn line from a crash: that unit is not marked done
            if record.get("status") == "done":
                keys.append(record["unit"])
    return keys


def completed_units_to_skip(progress_path, safety_window, results_root):
    """Units safe to skip on resume: marked done, outside the safety window, with their output on disk."""
    keys = _read_done_keys(progress_path)
    trusted = keys[:-safety_window] if safety_window > 0 else keys
    return {
        key
        for key in trusted
        if os.path.exists(unit_output_path(results_root, *split_unit_key(key)))
    }


def save_manifest(path, manifest):
    write_json_atomic(path, manifest)


def load_manifest(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def raise_if_paused(pause_file, stop_flag):
    if stop_flag() or os.path.exists(pause_file):
        raise PauseRequested("pause requested")
