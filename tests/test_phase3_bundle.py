import json

import pytest

from phase3 import config
from phase3.bundle import build_bundle
from phase3.runs import run_paths, write_state

PROBE = {"network": "Bharti Airtel", "asn": 24560, "allocation": 9, "telecom": True}
STATUS = {"updated_at": "2026-11-08T08:00:00+00:00", "run": 1, "run_status": "paused",
          "units_done": 2, "units_total": 2, "running_on": None, "heartbeat": None, "last_writer": "local"}


@pytest.fixture
def scan(tmp_path, monkeypatch):
    output = tmp_path / "phase3"
    targets = tmp_path / "phase3_targets_selected.csv"
    targets.write_text(
        "Target IP,City,ISP,Tier\n"
        "10.0.0.1,Pune,Bharti Airtel Limited,telecom\n"
        "10.0.0.2,Pune,Amazon.com Inc,cloud\n",
        encoding="utf-8",
    )
    manifest = {"target_count": 2, "traceroutes_total": 18, "plan": {"cities": {"Pune": {"probes": [PROBE]}}}}
    output.mkdir()
    (output / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(config, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(config, "MANIFEST_FILE", str(output / "run_manifest.json"))
    monkeypatch.setattr(config, "TARGETS_CSV", str(targets))
    paths = run_paths(str(output), 1)
    write_state(paths, "paused", 2)
    with open(paths["progress"], "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"unit": "10.0.0.1|Pune", "status": "done"}) + "\n")
        fh.write(json.dumps({"unit": "10.0.0.2|Pune", "status": "done"}) + "\n")
    return output


def test_bundle_has_the_overview_plan_targets_and_runs(scan):
    bundle = build_bundle(STATUS, with_failures=False)

    assert bundle["overview"]["units_done"] == 2
    assert bundle["city_progress"] == [{"Probe_City": "Pune", "Done": 2, "Of": 2, "Percent": 100.0}]
    assert bundle["plan"][0]["Probe_City"] == "Pune"
    assert bundle["targets"]["count"] == 2
    assert [row["Tier"] for row in bundle["targets"]["cities"]["Pune"]] == ["Telecom", "Cloud / hosting"]
    assert bundle["runs"] == [{"Run": "run_01", "Status (state file)": "paused", "Units total": 2}]
    assert bundle["commands"][0]["command"] == "python -m phase3 sync pull"


def test_failures_are_left_out_on_a_heartbeat_and_included_on_a_push(scan):
    assert build_bundle(STATUS, with_failures=False)["failures"] is None

    failures = build_bundle(STATUS, with_failures=True)["failures"]

    assert failures["count"] == 0
    assert failures["rows"] == []


def test_logs_show_the_latest_progress_lines(scan):
    logs = build_bundle(STATUS, with_failures=False)["logs"]

    assert len(logs["progress"]) == 2
    assert logs["console"] == []
