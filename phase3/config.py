"""Phase 3 settings. Fresh config: nothing here is read from config/ or the older phases."""

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGETS_CSV = os.path.join(
    PROJECT_ROOT, "output", "target_cities", "phase3_selection", "phase3_targets_selected.csv"
)
COUNTRY = "IN"

# Each run writes to OUTPUT_DIR/run_NN/ (see phase3/runs.py).
OUTPUT_DIR = os.environ.get("PHASE3_OUTPUT_DIR", os.path.join(PROJECT_ROOT, "output", "phase3"))
MANIFEST_FILE = os.path.join(OUTPUT_DIR, "run_manifest.json")
PAUSE_FILE = os.path.join(OUTPUT_DIR, "PAUSE")

TRACEROUTES_PER_IP_CITY = 9
MAX_PROBES_PER_CITY = 3

# On resume, the last N completed units are re-run and overwritten. This covers
# a unit that was finished on disk but whose progress line was lost in a crash.
SAFETY_WINDOW_UNITS = 20

# Probe cities merged into one city for the run.
CITY_ALIASES = {"New Delhi": "Delhi"}

# Telecom operators, by ASN. Probes on these are preferred over cloud/hosting probes.
TELECOM_ASNS = frozenset(
    {
        24560,  # Bharti Airtel
        9829,  # BSNL (National Internet Backbone)
        55836,  # Reliance Jio
        24309,  # ACT Fibernet (Atria Convergence)
        18209,  # ACT Fibernet (Atria Convergence)
        23860,  # Alliance Broadband
        133982,  # Excitel
        140159,  # Linkio Fibernet
        141253,  # Hyosec Solutions
        55352,  # Microscan Internet
        132335,  # Leapswitch Networks
        153252,  # Elxire
        45775,  # Wish Net
        135801,  # C32 Broadband
        136342,  # Winux Communications
        151732,  # OnRemote Telecom
        17465,  # Cable ISP in India
        45609,  # Bharti Airtel GPRS (mobile)
    }
)

GLOBALPING_MEASUREMENTS_URL = "https://api.globalping.io/v1/measurements"
GLOBALPING_PROBES_URL = "https://api.globalping.io/v1/probes"
TOKEN_ENV_VAR = "GLOBALPING_TOKENS"  # comma-separated, read from the environment only
# Assumed cooldowns: a token that hits a rate limit rests for a minute; one rejected as invalid rests for an hour.
TOKEN_RATE_LIMIT_COOLDOWN_SEC = 60
TOKEN_REJECTED_COOLDOWN_SEC = 3600
# A token that reports insufficient_credits is skipped for this long, in case its credits are topped up.
TOKEN_CREDITS_COOLDOWN_SEC = 3600
# When every token is out of credits, wait for the first one to recover and carry on by itself.
# Set to False to stop the run instead (then resume by hand).
CREDITS_AUTO_RESUME = True
CONCURRENCY = 8  # parallel units; 8 is the default until a benchmark shows 12 is better
# The supervisor restarts a crashed run after this wait, doubling each time up to the cap.
SUPERVISOR_BACKOFF_SEC = 30
SUPERVISOR_MAX_BACKOFF_SEC = 600
POLL_INTERVAL_SEC = 3
MAX_POLLS = 40
MAX_RETRIES = 6
INITIAL_BACKOFF_SEC = 5
# A probe city+ASN with no connected probe is not asked again for this long (one check per cooldown).
PROBE_UNAVAILABLE_COOLDOWN_SEC = 1800
# If a probe stays missing this long, its units are recorded as no_probe and the run moves on.
PROBE_GIVE_UP_AFTER_SEC = 24 * 3600
# How often a run with only parked units checks whether a cooldown has ended.
PROBE_WAIT_POLL_SEC = 5
# If this many measurements fail in a row (Globalping not finishing them), the run pauses instead of retrying forever.
CONSECUTIVE_FAILURE_LIMIT = 20
