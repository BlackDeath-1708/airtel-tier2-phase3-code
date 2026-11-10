import json
from pathlib import Path

import pandas as pd

from gui import phase3_data as p3


def _record():
    return {
        "ip": "203.0.113.5",
        "city": "Delhi",
        "rounds": [
            {"round": 1, "measurements": [{
                "probe": {"asn": 9829, "network": "BSNL"},
                "status": "finished",
                "hops": [
                    {"resolvedAddress": "10.0.0.1", "resolvedHostname": "gw", "timings": [{"rtt": 1.0}, {"rtt": 3.0}]},
                    {"resolvedAddress": None, "resolvedHostname": None, "timings": []},
                ],
            }]},
            {"round": 2, "measurements": [{
                "probe": {"asn": 9829, "network": "BSNL"},
                "status": "failed",
                "hops": [],
            }]},
        ],
    }


def test_traceroute_frame_keeps_silent_hops_as_star_rows_with_averaged_rtt():
    frame = p3.traceroute_frame(_record())

    first = frame[frame["Hop"] == 1].iloc[0]
    silent = frame[frame["Hop"] == 2].iloc[0]
    assert first["Avg_RTT_ms"] == 2.0
    assert silent["Hop_IP"] == "*"
    assert pd.isna(silent["Avg_RTT_ms"])


def test_traceroute_labels_are_numbered_across_rounds():
    summary = p3.traceroute_summary_frame(_record())

    assert list(summary["Traceroute"]) == ["T1", "T2"]
    assert list(summary["Reached"]) == [True, False]
    assert list(p3.traceroute_frame(_record())["Traceroute"].unique()) == ["T1"]


def test_iteration_options_name_the_probe_isp_for_each_traceroute():
    options = dict(p3.traceroute_options(_record()))

    assert options["T1"] == "T1 · BSNL (AS9829) · round 1"
    assert options["T2"] == "T2 · BSNL (AS9829) · round 2"


def test_iteration_label_falls_back_to_the_plan_network_when_the_record_has_none():
    record = _record()
    record["rounds"][0]["measurements"][0]["probe"] = {"asn": 45609}
    networks = {45609: "Bharti Airtel Ltd. AS for GPRS Service"}

    options = dict(p3.traceroute_options(record, networks))

    assert options["T1"] == "T1 · Bharti Airtel Ltd. AS for GPRS Service (AS45609) · round 1"


def test_asn_network_lookup_maps_each_planned_asn_to_its_network():
    manifest = {"plan": {"cities": {"Delhi": {"probes": [{"asn": 9829, "network": "BSNL"}]}}}}

    assert p3.asn_network_lookup(manifest) == {9829: "BSNL"}


def test_traceroute_frame_carries_the_iteration_label_for_the_graph_legend():
    frame = p3.traceroute_frame(_record())

    assert set(frame["Iteration"]) == {"T1 · BSNL (AS9829) · round 1"}


def test_read_done_units_skips_a_torn_last_line(tmp_path):
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        json.dumps({"unit": "1.1.1.1|Delhi", "status": "done"}) + "\n"
        + '{"unit": "2.2.2.2|De'
    )

    assert p3.read_done_units(str(progress)) == {"1.1.1.1|Delhi"}


def test_read_done_units_is_empty_when_no_progress_file_exists(tmp_path):
    assert p3.read_done_units(str(tmp_path / "missing.jsonl")) == set()


def test_city_progress_frame_counts_done_units_per_probe_city():
    frame = p3.city_progress_frame({"1.1.1.1|Delhi", "2.2.2.2|Delhi"}, 4, ["Delhi", "Mumbai"]).set_index("Probe_City")

    assert frame.loc["Delhi", "Done"] == 2
    assert frame.loc["Mumbai", "Percent"] == 0.0


def test_rate_is_per_minute_and_eta_uses_that_rate():
    rate = p3.rate_per_min([(0.0, 100), (60.0, 130)])

    assert rate == 30.0
    assert p3.eta_seconds(130, 190, rate) == 120.0
    assert p3.eta_seconds(130, 190, 0) is None


def test_probe_plan_frame_lists_each_probe_with_its_tier():
    manifest = {"plan": {"cities": {"Delhi": {"probes": [
        {"network": "BSNL", "asn": 9829, "allocation": 5, "telecom": True},
        {"network": "Oracle", "asn": 31898, "allocation": 4, "telecom": False},
    ]}}}}

    frame = p3.probe_plan_frame(manifest)

    assert list(frame["Tier"]) == ["Telecom", "Cloud / other"]
    assert list(frame["Traceroutes_per_IP"]) == [5, 4]


def test_tier_mix_frame_counts_targets_per_city_and_tier():
    targets = pd.DataFrame({"City": ["Delhi", "Delhi", "Delhi"], "Tier": ["telecom", "telecom", "cloud"]})

    frame = p3.tier_mix_frame(targets)

    counts = {(r.City, r.Tier): r.IPs for r in frame.itertuples()}
    assert counts == {("Delhi", "telecom"): 2, ("Delhi", "cloud"): 1}


def _targets():
    return pd.DataFrame({
        "Target IP": ["1.1.1.1", "2.2.2.2", "3.3.3.3", "4.4.4.4"],
        "City": ["Delhi", "Delhi", "Delhi", "Mumbai"],
        "ISP": ["BSNL", "BSNL", "Amazon", "Jio"],
        "Tier": ["telecom", "telecom", "cloud", "telecom"],
    })


def test_city_targets_frame_lists_only_that_citys_ips_with_readable_tiers():
    frame = p3.city_targets_frame(_targets(), "Delhi")

    assert list(frame["Target IP"]) == ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
    assert list(frame["Tier"]) == ["Telecom", "Telecom", "Cloud / hosting"]


def test_city_isp_counts_frame_counts_ips_per_isp_in_one_city():
    frame = p3.city_isp_counts_frame(_targets(), "Delhi")

    counts = {(r.ISP, r.Tier): r.IPs for r in frame.itertuples()}
    assert counts == {("BSNL", "Telecom"): 2, ("Amazon", "Cloud / hosting"): 1}


def test_graph_target_catalog_lists_only_measured_ips_with_isp_and_tier_labels():
    frame = p3.graph_target_catalog(_targets(), {"1.1.1.1", "3.3.3.3"})

    assert list(frame["Target IP"]) == ["1.1.1.1", "3.3.3.3"]
    assert list(frame["City"]) == ["Delhi", "Delhi"]
    assert list(frame["Label"]) == ["1.1.1.1 · BSNL · Telecom", "3.3.3.3 · Amazon · Cloud / hosting"]


def test_graph_target_catalog_is_empty_when_nothing_is_measured_yet():
    assert p3.graph_target_catalog(_targets(), set()).empty


def test_request_pause_creates_the_pause_file(tmp_path):
    pause = tmp_path / "PAUSE"

    p3.request_pause(str(pause))

    assert p3.pause_requested(str(pause))


