"""Globalping client: one traceroute per call, with token rotation, backoff and polling."""

import threading
import time

import requests

from phase3 import config


class RequestFailed(Exception):
    """A Globalping request kept failing after all retries."""


class CreditsExhausted(Exception):
    """Every Globalping token is out of credits. retry_at is when the first of them may work again (monotonic clock)."""

    def __init__(self, retry_at):
        super().__init__("every Globalping token reports insufficient_credits")
        self.retry_at = retry_at


class ProbeUnavailable(Exception):
    """No probe matches this (city, ASN) right now. The unit should wait, not be recorded as finished."""

    def __init__(self, key):
        super().__init__(f"no probe in {key[0]} for AS{key[1]} right now")
        self.key = key


def no_probe_result(location):
    return {"probe": {"city": location["city"], "asn": location["asn"]}, "status": "no_probe", "hops": []}


class GlobalpingClient:
    def __init__(self, tokens, session=None, sleep=time.sleep, clock=time.monotonic):
        if not tokens:
            raise ValueError(f"no Globalping tokens: set {config.TOKEN_ENV_VAR}")
        self._tokens = list(tokens)
        self._token_index = 0
        self._cool_until = [0.0] * len(self._tokens)
        self._dry_until = [0.0] * len(self._tokens)  # token out of credits until this time
        self._lock = threading.Lock()
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._unavailable = {}  # (city, ASN) -> {"since": first missing, "retry_at": next check}

    def _headers(self):
        with self._lock:
            token = self._tokens[self._token_index]
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "airtel-phase3",
        }

    def _rotate_token(self, cooldown_sec=None):
        """Parks the current token for cooldown_sec (if given) and moves to the next token that is ready."""
        with self._lock:
            now = time.monotonic()
            if cooldown_sec is not None:
                self._cool_until[self._token_index] = now + cooldown_sec
            count = len(self._tokens)
            for step in range(1, count + 1):
                candidate = (self._token_index + step) % count
                if self._cool_until[candidate] <= now:
                    self._token_index = candidate
                    return
            # Every token is cooling down: use the one that frees up first.
            self._token_index = min(range(count), key=lambda i: self._cool_until[i])

    def _mark_out_of_credits(self):
        """Skips the current token for a while and moves on. Raises CreditsExhausted once every token is out."""
        with self._lock:
            now = time.monotonic()
            self._dry_until[self._token_index] = now + config.TOKEN_CREDITS_COOLDOWN_SEC
            self._cool_until[self._token_index] = self._dry_until[self._token_index]
            if all(until > now for until in self._dry_until):
                raise CreditsExhausted(min(self._dry_until))
        self._rotate_token()

    def traceroute(self, target, location):
        """One traceroute from the probe selector `location` (city + ASN) to `target`.

        Raises ProbeUnavailable without calling Globalping while that city and ASN are marked unavailable.
        """
        key = (location["city"], location["asn"])
        if self._is_blocked(key):
            raise ProbeUnavailable(key)
        body = {
            "type": "traceroute",
            "target": target,
            "locations": [{"country": config.COUNTRY, "city": location["city"], "asn": location["asn"]}],
            "limit": 1,
        }
        measurement_id = self._create(body)
        if measurement_id is None:
            self._mark_unavailable(key)
            raise ProbeUnavailable(key)
        self._mark_available(key)
        return self._poll(measurement_id, location)

    def unavailable(self, key):
        """The unavailable state of a (city, ASN) pair, or None when it is not marked unavailable."""
        with self._lock:
            state = self._unavailable.get(key)
            return dict(state) if state else None

    def restore_unavailable(self, key, since, retry_at):
        """Puts back a pair's cooldown from before a restart, using this process's monotonic clock."""
        with self._lock:
            self._unavailable[key] = {"since": since, "retry_at": retry_at}

    def _is_blocked(self, key):
        state = self.unavailable(key)
        return state is not None and self._clock() < state["retry_at"]

    def _mark_unavailable(self, key):
        now = self._clock()
        with self._lock:
            since = self._unavailable[key]["since"] if key in self._unavailable else now
            self._unavailable[key] = {"since": since, "retry_at": now + config.PROBE_UNAVAILABLE_COOLDOWN_SEC}

    def _mark_available(self, key):
        with self._lock:
            self._unavailable.pop(key, None)

    def _create(self, body):
        backoff = config.INITIAL_BACKOFF_SEC
        for _ in range(config.MAX_RETRIES):
            try:
                response = self._session.post(
                    config.GLOBALPING_MEASUREMENTS_URL, json=body, headers=self._headers(), timeout=60
                )
            except requests.RequestException:
                self._sleep(backoff)  # DNS or network failure: wait and try again
                backoff *= 2
                continue
            if response.status_code in (200, 201, 202):
                return response.json()["id"]
            if response.status_code == 422:
                return None  # no matching probe is connected right now
            if response.status_code == 429 and _is_out_of_credits(response):
                self._mark_out_of_credits()
                return self._create(body)  # the next token gets a fresh set of attempts
            if response.status_code in (401, 403):
                self._rotate_token(config.TOKEN_REJECTED_COOLDOWN_SEC)
            elif response.status_code == 429:
                self._rotate_token(config.TOKEN_RATE_LIMIT_COOLDOWN_SEC)
            self._sleep(backoff)
            backoff *= 2
        raise RequestFailed(f"measurement create failed after {config.MAX_RETRIES} attempts")

    def _poll(self, measurement_id, location):
        url = f"{config.GLOBALPING_MEASUREMENTS_URL}/{measurement_id}"
        for _ in range(config.MAX_POLLS):
            try:
                response = self._session.get(url, headers=self._headers(), timeout=60)
            except requests.RequestException:
                self._sleep(config.POLL_INTERVAL_SEC)  # network blip: count it as a poll and keep going
                continue
            if response.status_code == 429:
                self._rotate_token(config.TOKEN_RATE_LIMIT_COOLDOWN_SEC)
            if response.status_code == 200 and response.json().get("status") != "in-progress":
                return _summarise(response.json(), location)
            self._sleep(config.POLL_INTERVAL_SEC)
        raise RequestFailed(f"measurement {measurement_id} did not finish")


def _is_out_of_credits(response):
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return False
    return error.get("type") == "insufficient_credits"


def _summarise(data, location):
    results = data.get("results") or []
    if not results:
        return no_probe_result(location)
    first = results[0]
    result = first.get("result") or {}
    return {
        "probe": first.get("probe") or {},
        "status": result.get("status", "unknown"),
        "hops": result.get("hops") or [],
    }


def fetch_india_probes(session=None):
    """Live Globalping probe directory, filtered to India. Returns plain probe records."""
    http = session or requests.Session()
    response = http.get(config.GLOBALPING_PROBES_URL, timeout=60, headers={"User-Agent": "airtel-phase3"})
    response.raise_for_status()
    probes = []
    for item in response.json():
        location = item.get("location") or {}
        if location.get("country") != config.COUNTRY:
            continue
        probes.append(
            {
                "country": location["country"],
                "city": location["city"],
                "asn": location["asn"],
                "network": location.get("network", ""),
            }
        )
    return probes
