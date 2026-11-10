from phase3 import config
from phase3.plan import is_telecom
from phase3.plan import (
    allocate_iterations,
    build_plan,
    is_telecom,
    rounds_for,
    select_probes,
    total_traceroutes,
)


def probe(city, asn, network):
    return {"country": "IN", "city": city, "asn": asn, "network": network}


def test_is_telecom_recognises_telecom_asns_and_rejects_cloud():
    assert is_telecom(24560) is True  # Airtel
    assert is_telecom(9829) is True  # BSNL
    assert is_telecom(16509) is False  # Amazon
    assert is_telecom(31898) is False  # Oracle


def test_select_probes_takes_telecom_first_then_cloud_in_network_order():
    pool = [
        probe("Mumbai", 31898, "Oracle"),
        probe("Mumbai", 24560, "Bharti Airtel"),
        probe("Mumbai", 16509, "Amazon"),
    ]

    chosen = select_probes(pool)

    assert [p["asn"] for p in chosen] == [24560, 16509, 31898]
    assert [p["telecom"] for p in chosen] == [True, False, False]


def test_select_probes_allows_repeated_telecom_networks_when_needed():
    pool = [
        probe("Bengaluru", 24560, "Bharti Airtel"),
        probe("Bengaluru", 24560, "Bharti Airtel"),
        probe("Bengaluru", 24309, "Atria"),
    ]

    chosen = select_probes(pool)

    assert len(chosen) == 3
    assert all(p["telecom"] for p in chosen)


def test_select_probes_caps_at_three_per_city():
    pool = [probe("Mumbai", 31898 + i, f"Net{i}") for i in range(6)]

    assert len(select_probes(pool)) == config.MAX_PROBES_PER_CITY


def test_cloud_fallback_spreads_across_networks_before_repeating():
    pool = [
        probe("Hyderabad", 31898, "Oracle"),
        probe("Hyderabad", 31898, "Oracle"),
        probe("Hyderabad", 16509, "Amazon"),
    ]

    chosen = select_probes(pool)

    networks = [p["network"] for p in chosen]
    assert sorted(networks) == ["Amazon", "Oracle", "Oracle"]
    assert len(set(networks[:2])) == 2


def test_allocate_iterations_sums_to_nine_for_each_probe_count():
    assert allocate_iterations(3) == [3, 3, 3]
    assert allocate_iterations(2) == [5, 4]
    assert allocate_iterations(1) == [9]
    assert sum(allocate_iterations(2)) == config.TRACEROUTES_PER_IP_CITY


def test_rounds_follow_allocation_and_drop_finished_probes():
    selectors = [
        {"city": "X", "asn": 1, "allocation": 5},
        {"city": "X", "asn": 2, "allocation": 4},
    ]

    rounds = rounds_for(selectors)

    assert len(rounds) == 5
    assert len(rounds[0]) == 2
    assert len(rounds[4]) == 1
    assert rounds[4][0]["asn"] == 1


def test_build_plan_merges_new_delhi_into_delhi():
    probes = [
        probe("New Delhi", 396982, "Google"),
        probe("Delhi", 24560, "Bharti Airtel"),
    ]

    plan = build_plan(probes)

    assert "New Delhi" not in plan["cities"]
    assert len(plan["cities"]["Delhi"]["probes"]) == 2


def test_total_traceroutes_is_ips_times_nine_times_cities():
    plan = build_plan([probe("Mumbai", 24560, "Bharti Airtel")])

    assert total_traceroutes(plan, ip_count=10) == 10 * 9 * 1


def test_airtel_gprs_asn_is_classed_as_telecom():
    assert is_telecom(45609)
