"""Telecom-first probe selection and the 9-traceroute schedule. Pure functions, no I/O."""

from phase3 import config


def is_telecom(asn):
    return asn in config.TELECOM_ASNS


def canonical_city(city):
    return config.CITY_ALIASES.get(city, city)


def _spread(probes):
    """Orders probes round-robin across networks, so a network repeats only after every other network has been used."""
    groups = {}
    for probe in sorted(probes, key=lambda p: (p["network"], p["asn"])):
        groups.setdefault(probe["network"], []).append(probe)

    ordered = []
    depth = 0
    while any(len(group) > depth for group in groups.values()):
        for network in sorted(groups):
            if len(groups[network]) > depth:
                ordered.append(groups[network][depth])
        depth += 1
    return ordered


def select_probes(pool):
    """Picks up to MAX_PROBES_PER_CITY probes: telecom first, then cloud/hosting."""
    telecom = [dict(p, telecom=True) for p in _spread([p for p in pool if is_telecom(p["asn"])])]
    cloud = [dict(p, telecom=False) for p in _spread([p for p in pool if not is_telecom(p["asn"])])]
    return (telecom + cloud)[: config.MAX_PROBES_PER_CITY]


def allocate_iterations(probe_count):
    """Splits the per-IP-city traceroutes across probes, e.g. 3 -> [3, 3, 3], 2 -> [5, 4], 1 -> [9]."""
    if probe_count == 0:
        return []
    base, extra = divmod(config.TRACEROUTES_PER_IP_CITY, probe_count)
    return [base + 1] * extra + [base] * (probe_count - extra)


def rounds_for(selectors):
    """Round r holds one traceroute for every probe that still has iterations left in round r."""
    total_rounds = max((s["allocation"] for s in selectors), default=0)
    return [
        [{"city": s["city"], "asn": s["asn"]} for s in selectors if s["allocation"] > index]
        for index in range(total_rounds)
    ]


def build_plan(probes):
    """Builds the frozen per-city probe plan from a list of probe records."""
    by_city = {}
    for probe in probes:
        if probe["country"] != config.COUNTRY:
            continue
        by_city.setdefault(canonical_city(probe["city"]), []).append(probe)

    cities = {}
    for city in sorted(by_city):
        chosen = select_probes(by_city[city])
        selectors = [
            dict(probe, allocation=allocation)
            for probe, allocation in zip(chosen, allocate_iterations(len(chosen)))
        ]
        cities[city] = {"probes": selectors, "rounds": len(rounds_for(selectors))}

    return {"traceroutes_per_ip_city": config.TRACEROUTES_PER_IP_CITY, "cities": cities}


def first_run_items(plan, targets):
    """Work list for the first run: every target IP in every planned probe city."""
    items = []
    for ip in targets:
        for city, info in plan["cities"].items():
            rounds = [{"round": index, "selectors": selectors} for index, selectors in enumerate(rounds_for(info["probes"]))]
            items.append({"ip": ip, "city": city, "rounds": rounds})
    return items


def total_traceroutes(plan, ip_count):
    per_ip = sum(sum(s["allocation"] for s in info["probes"]) for info in plan["cities"].values())
    return ip_count * per_ip
