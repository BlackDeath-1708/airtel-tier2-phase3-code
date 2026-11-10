"""Live view of the Phase 3 scan, for a phone or a browser. Reads the summary the scan publishes to GitHub.

Settings (Streamlit secrets, or environment variables with the same names):
  GITHUB_REPO        owner/name of the private repo
  GITHUB_READ_TOKEN  a read-only token for that repo
  APP_PASSWORD       the page's password
"""

import datetime
import os
import secrets
import sys

import requests
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.github_source import STATUS_NAME, fetch_asset, fetch_view  # noqa: E402
from live.status_data import progress_percent, scan_health, to_ist  # noqa: E402

REFRESH_SEC = 300
HEALTH_TEXT = {
    "running": "Running",
    "stalled": "Stalled: the runner stopped reporting",
    "paused": "Paused",
    "complete": "Complete",
    "idle": "Idle",
}

st.set_page_config(page_title="Airtel Phase 3 live scan", layout="wide")


def setting(name):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets file on this machine: fall back to the environment
        pass
    return os.environ.get(name, "")


def check_password():
    expected = setting("APP_PASSWORD")
    if not expected:
        st.error("APP_PASSWORD is not set, so the page stays locked.")
        st.stop()
    if st.session_state.get("password_correct"):
        return

    def password_entered():
        entered = st.session_state.get("password_input", "")
        st.session_state["password_correct"] = secrets.compare_digest(entered, expected)

    st.text_input("Password", type="password", on_change=password_entered, key="password_input")
    if st.session_state.get("password_correct") is False:
        st.error("Incorrect password")
    st.stop()


@st.cache_data(ttl=60, show_spinner=False)
def load_view(repo, token):
    try:
        return fetch_view(repo, token)
    except LookupError:
        # A scan that started before the full summary existed only publishes its status file.
        status = fetch_asset(repo, token, STATUS_NAME)
        return {"status": status, "generated_at": status.get("updated_at"), "overview": {},
                "city_progress": [], "plan": [], "targets": {}, "parked": {}, "runs": [],
                "failures": None, "logs": {}, "commands": []}


def show_table(records, empty_text="Nothing here yet."):
    if records:
        st.dataframe(records, hide_index=True, width="stretch")
    else:
        st.info(empty_text)


def overview_tab(view):
    status = view["status"]
    overview = view.get("overview") or {}
    health = scan_health(status, datetime.datetime.now(datetime.timezone.utc))
    done = status.get("units_done", 0)
    total = status.get("units_total") or 0
    percent = progress_percent(done, total)
    st.subheader(HEALTH_TEXT.get(health, health))
    st.progress(percent / 100, text=f"{done:,} of {total:,} units ({percent:.1f}%)")
    col_status, col_holder, col_updated, col_traceroutes = st.columns(4)
    col_status.metric("Run status", status.get("run_status") or "n/a")
    col_holder.metric("Held by", status.get("running_on") or "nobody")
    col_updated.metric("Last update", to_ist(status.get("updated_at")))
    col_traceroutes.metric("Traceroutes done", f"{overview.get('traceroutes_done', 0):,} of {overview.get('traceroutes_total', 0):,}")
    st.caption(f"Last heartbeat: {to_ist(status.get('heartbeat'))}. Written by: {status.get('last_writer') or 'n/a'}. "
               f"Published: {to_ist(view.get('generated_at'))}.")
    if not overview:
        st.info("This run only publishes its status so far. The tables appear after the next run that uploads the full view.")
        return
    st.subheader("Progress per probe city")
    show_table(view.get("city_progress", []))


def parked_tab(view):
    parked = view.get("parked") or {}
    pairs = parked.get("pairs", [])
    parked_now = sum(pair.get("parked_units", 0) for pair in pairs)
    col_parked, col_released, col_gave_up, col_snapshot = st.columns(4)
    col_parked.metric("Units parked now", f"{parked_now:,}")
    col_released.metric("Released for retry", f"{parked.get('released_for_retry', 0):,}")
    col_gave_up.metric("Gave up", f"{parked.get('gave_up', 0):,}")
    col_snapshot.metric("Last snapshot", to_ist(parked.get("updated_at")))
    st.caption("A unit is parked when its probe city has no connected probe. Parked units are tried again after a "
               "cooldown. A probe missing for 24 hours is given up on, and its units are recorded as no_probe.")
    st.subheader("Probes that are missing")
    show_table(pairs, "No probe is missing right now.")
    st.subheader("Parked units")
    show_table(parked.get("units", []), "No units are parked.")
    st.subheader("What happened (newest first)")
    show_table(parked.get("events", []), "No parked-unit events yet.")


def plan_tab(view):
    st.subheader("Frozen probe plan")
    show_table(view.get("plan", []), "No plan yet.")


def targets_tab(view):
    targets = view.get("targets") or {}
    st.metric("Target IPs", f"{targets.get('count', 0):,}")
    st.subheader("Selection summary")
    show_table(targets.get("selection_summary", []))
    st.subheader("Tier mix by city")
    show_table(targets.get("tier_mix", []))
    cities = sorted((targets.get("cities") or {}).keys())
    if not cities:
        st.info("No target list yet.")
        return
    city = st.selectbox("City", cities, key="live_target_city")
    st.subheader(f"ISPs in {city}")
    show_table((targets.get("city_isp") or {}).get(city, []))
    st.subheader(f"Target IPs in {city}")
    show_table(targets["cities"][city])


def runs_tab(view):
    st.subheader("Runs")
    show_table(view.get("runs", []), "No runs yet.")


def failures_tab(view):
    failures = view.get("failures")
    if failures is None:
        st.info("The failures list is built when a run is pushed, so it may be from the last push.")
        return
    st.metric("Failed traceroutes", f"{failures.get('count', 0):,}")
    st.write("By status: " + (", ".join(f"{k}: {v:,}" for k, v in failures.get("by_status", {}).items()) or "none"))
    show_table(failures.get("rows", []), "No failed traceroutes.")


def logs_tab(view):
    logs = view.get("logs") or {}
    st.subheader("Scan console (latest lines)")
    st.code("\n".join(logs.get("console", [])) or "No console log uploaded yet.", language="text")
    st.subheader("Progress log (latest lines)")
    st.code("\n".join(logs.get("progress", [])) or "Empty.", language="json")
    st.subheader("Parked-unit event log (latest lines)")
    st.code("\n".join(logs.get("events", [])) or "Empty.", language="json")


def commands_tab(view):
    st.subheader("How to control the scan")
    show_table(view.get("commands", []))


@st.fragment(run_every=REFRESH_SEC)
def live_view():
    repo, token = setting("GITHUB_REPO"), setting("GITHUB_READ_TOKEN")
    if not repo or not token:
        st.error("GITHUB_REPO and GITHUB_READ_TOKEN must be set.")
        return
    try:
        view = load_view(repo, token)
    except (requests.RequestException, LookupError, ValueError) as error:
        st.error(f"Could not read the scan from GitHub: {error}")
        return
    tabs = st.tabs(["Overview", "Parked units", "Probe plan", "Targets", "Runs", "Failures", "Logs", "Commands"])
    with tabs[0]:
        overview_tab(view)
    with tabs[1]:
        parked_tab(view)
    with tabs[2]:
        plan_tab(view)
    with tabs[3]:
        targets_tab(view)
    with tabs[4]:
        runs_tab(view)
    with tabs[5]:
        failures_tab(view)
    with tabs[6]:
        logs_tab(view)
    with tabs[7]:
        commands_tab(view)


st.title("Airtel Phase 3: live scan")
check_password()
live_view()
