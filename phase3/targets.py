"""Loads the target IP list (deduplicated, file order kept) and fingerprints the source file."""

import csv
import hashlib


def load_targets(path, limit=None):
    seen = set()
    ips = []
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ip = row["Target IP"].strip()
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)
    return ips[:limit] if limit else ips


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
