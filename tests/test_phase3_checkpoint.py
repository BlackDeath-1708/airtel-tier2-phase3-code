import json
import os

import pytest

from phase3 import checkpoint
from phase3.checkpoint import (
    PauseRequested,
    completed_units_to_skip,
    load_manifest,
    raise_if_paused,
    save_manifest,
    unit_output_path,
)


def write_progress_and_outputs(progress, results_root, units):
    with open(progress, "w", encoding="utf-8") as fh:
        for ip, city in units:
            fh.write(json.dumps({"unit": f"{ip}|{city}", "status": "done"}) + "\n")
            path = unit_output_path(results_root, ip, city)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as out:
                out.write("{}")


def test_safety_window_reruns_the_last_completed_units(tmp_path):
    progress = str(tmp_path / "progress.jsonl")
    results_root = str(tmp_path / "results")
    units = [(f"10.0.0.{i}", "Mumbai") for i in range(10)]
    write_progress_and_outputs(progress, results_root, units)

    skip = completed_units_to_skip(progress, safety_window=3, results_root=results_root)

    assert "10.0.0.0|Mumbai" in skip
    assert "10.0.0.6|Mumbai" in skip
    assert "10.0.0.7|Mumbai" not in skip
    assert "10.0.0.9|Mumbai" not in skip


def test_zero_safety_window_skips_everything_already_done(tmp_path):
    progress = str(tmp_path / "progress.jsonl")
    results_root = str(tmp_path / "results")
    write_progress_and_outputs(progress, results_root, [("10.0.0.1", "Mumbai")])

    skip = completed_units_to_skip(progress, safety_window=0, results_root=results_root)

    assert skip == {"10.0.0.1|Mumbai"}


def test_units_with_missing_output_file_are_rerun(tmp_path):
    progress = str(tmp_path / "progress.jsonl")
    with open(progress, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"unit": "1.1.1.1|Mumbai", "status": "done"}) + "\n")

    skip = completed_units_to_skip(progress, safety_window=0, results_root=str(tmp_path / "results"))

    assert "1.1.1.1|Mumbai" not in skip


def test_missing_progress_file_means_nothing_is_skipped(tmp_path):
    skip = completed_units_to_skip(
        str(tmp_path / "absent.jsonl"), safety_window=5, results_root=str(tmp_path)
    )

    assert skip == set()


def test_manifest_round_trip(tmp_path):
    path = str(tmp_path / "manifest.json")
    save_manifest(path, {"plan": {"cities": {}}, "targets_sha256": "abc"})

    loaded = load_manifest(path)

    assert loaded["targets_sha256"] == "abc"


def test_pause_file_raises_pause_requested(tmp_path):
    pause_file = str(tmp_path / "PAUSE")
    open(pause_file, "w").close()

    with pytest.raises(PauseRequested):
        raise_if_paused(pause_file, stop_flag=lambda: False)


def test_stop_flag_raises_pause_requested_without_pause_file(tmp_path):
    with pytest.raises(PauseRequested):
        raise_if_paused(str(tmp_path / "PAUSE"), stop_flag=lambda: True)


def test_no_pause_requested_does_not_raise(tmp_path):
    raise_if_paused(str(tmp_path / "PAUSE"), stop_flag=lambda: False)


def test_unit_output_path_slugs_city_name():
    path = unit_output_path("/r", "8.8.8.8", "Bengaluru")

    assert path == os.path.join("/r", "bengaluru", "8.8.8.8.json")
