import datetime
import os
import shutil
import subprocess

import pytest

from phase3 import config, sync


def _iso_ago(seconds):
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds")


@pytest.fixture
def scan_folder(tmp_path, monkeypatch):
    folder = tmp_path / "phase3"
    monkeypatch.setattr(config, "OUTPUT_DIR", str(folder))
    published = []
    monkeypatch.setattr(sync, "_publish_status", published.append)
    return folder, published


def test_lock_refuses_while_another_runner_has_a_fresh_heartbeat(scan_folder, monkeypatch):
    _, published = scan_folder
    monkeypatch.setattr(sync, "read_status", lambda: {"running_on": "github", "heartbeat": _iso_ago(60)})

    with pytest.raises(SystemExit, match="already running on github"):
        sync.lock("local")

    assert published == []


def test_lock_takes_over_when_the_other_heartbeat_is_stale(scan_folder, monkeypatch):
    _, published = scan_folder
    monkeypatch.setattr(sync, "read_status", lambda: {"running_on": "github", "heartbeat": _iso_ago(3 * 3600)})

    sync.lock("local")

    assert published[-1]["running_on"] == "local"


def test_pack_and_unpack_keep_the_progress_and_leave_out_the_pause_file(scan_folder, tmp_path):
    folder, _ = scan_folder
    run = folder / "run_01"
    run.mkdir(parents=True)
    (run / "progress.jsonl").write_text("unit-a\nunit-b\n", encoding="utf-8")
    (folder / "PAUSE").write_text("", encoding="utf-8")
    archive = str(tmp_path / "state.tar.gz")

    sync._pack(archive)
    shutil.rmtree(folder)
    sync._unpack(archive)

    assert (run / "progress.jsonl").read_text(encoding="utf-8") == "unit-a\nunit-b\n"
    assert not os.path.exists(folder / "PAUSE")


def test_units_done_counts_each_unit_once_even_when_a_resume_repeats_a_line(tmp_path):
    progress = tmp_path / "progress.jsonl"
    progress.write_text("unit-a\nunit-b\nunit-a\n\n", encoding="utf-8")

    assert sync.units_done(str(progress)) == 2


class _Beats:
    """Stands in for the stop event: wait() returns False for each beat, then True to stop the loop."""

    def __init__(self, beats):
        self.left = beats

    def wait(self, _timeout):
        if self.left == 0:
            return True
        self.left -= 1
        return False


@pytest.fixture
def beating(scan_folder, monkeypatch):
    _, published = scan_folder
    uploads = []
    monkeypatch.setattr(sync, "HEARTBEAT_SEC", 0)
    monkeypatch.setattr(sync, "current_status", lambda owner, running_on: {"running_on": running_on})
    monkeypatch.setattr(sync, "_upload_archive", lambda: uploads.append("state"))
    return published, uploads


def test_heartbeat_saves_the_full_state_every_few_beats(beating):
    published, uploads = beating

    sync.heartbeat_loop("github", _Beats(7))

    assert len(published) == 7
    assert len(uploads) == 7 // sync.CHECKPOINT_EVERY


def test_a_failed_save_does_not_stop_the_heartbeat(beating, monkeypatch):
    published, uploads = beating
    failures = iter([sync.subprocess.CalledProcessError(1, "gh")])

    def flaky_upload():
        error = next(failures, None)
        if error:
            raise error
        uploads.append("state")

    monkeypatch.setattr(sync, "_upload_archive", flaky_upload)

    sync.heartbeat_loop("github", _Beats(sync.CHECKPOINT_EVERY))

    assert len(published) == sync.CHECKPOINT_EVERY
    assert uploads == []


def test_pull_downloads_the_target_list_when_this_runner_has_none(scan_folder, tmp_path, monkeypatch):
    targets = tmp_path / "targets" / "phase3_targets_selected.csv"
    monkeypatch.setattr(config, "TARGETS_CSV", str(targets))
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if "--pattern" in args and args[args.index("--pattern") + 1] == sync.STATE_ARCHIVE:
            raise subprocess.CalledProcessError(1, "gh")  # no scan state in the release yet
        if "download" in args:
            download_dir = args[args.index("--dir") + 1]
            with open(os.path.join(download_dir, sync.TARGETS_NAME), "w", encoding="utf-8") as fh:
                fh.write("Target IP,City,ISP,Tier\n")
        return ""

    monkeypatch.setattr(sync, "_gh", fake_gh)
    monkeypatch.setattr(sync, "read_status", lambda: {})

    sync.pull()

    assert targets.read_text(encoding="utf-8") == "Target IP,City,ISP,Tier\n"


def test_pull_keeps_a_target_list_that_is_already_here(scan_folder, tmp_path, monkeypatch):
    targets = tmp_path / "phase3_targets_selected.csv"
    targets.write_text("already here\n", encoding="utf-8")
    monkeypatch.setattr(config, "TARGETS_CSV", str(targets))
    calls = []
    def fake_gh(*args):
        calls.append(args)
        raise subprocess.CalledProcessError(1, "gh")  # no scan state in the release yet

    monkeypatch.setattr(sync, "_gh", fake_gh)
    monkeypatch.setattr(sync, "read_status", lambda: {})

    sync.pull()

    assert not any(sync.TARGETS_NAME in c for c in calls)
    assert targets.read_text(encoding="utf-8") == "already here\n"


def test_heartbeat_calls_the_progress_callback_every_two_hours(beating):
    calls = []

    sync.heartbeat_loop("github", _Beats(2 * sync.PROGRESS_EVERY_BEATS), on_progress=lambda: calls.append("update"))

    assert len(calls) == 2


def test_a_failed_progress_update_does_not_stop_the_heartbeat(beating, capsys):
    published, _ = beating

    def broken():
        raise RuntimeError("discord is down")

    sync.heartbeat_loop("github", _Beats(sync.PROGRESS_EVERY_BEATS), on_progress=broken)

    assert len(published) == sync.PROGRESS_EVERY_BEATS
    assert "RuntimeError" in capsys.readouterr().out
