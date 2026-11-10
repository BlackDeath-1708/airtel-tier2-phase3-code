"""Posts a scan alert to a Discord channel when a scan run pauses, completes or fails.

The setting comes from the environment: DISCORD_WEBHOOK_URL. The URL is never printed: a failed send
reports only the error type, because a request error can contain the URL.
"""

import argparse
import os
import sys

import requests

from phase3 import sync

DISCORD_MESSAGE_LIMIT = 2000
DISCORD_OK_STATUSES = (200, 204)


def _progress_heading(status):
    if status is None:
        return "Phase 3 scan update: status unavailable"
    state = "running" if status.get("running_on") else "not running"
    return f"Phase 3 scan update: {state} ({status.get('run_status') or 'no run yet'})"


def _result_heading(outcome, finished):
    if outcome == "success" and finished:
        return "Phase 3 scan COMPLETE"
    if outcome == "success":
        return "Phase 3 scan paused at its time limit; the next run starts automatically"
    return f"Phase 3 scan STOPPED with an error ({outcome})"


def build_message(outcome, finished, status, run_url):
    """A progress update (outcome "progress") on the schedule, or the result of a run."""
    if outcome == "progress":
        lines = [_progress_heading(status)]
    else:
        lines = [_result_heading(outcome, finished)]
    if status and status.get("units_total"):
        done, total = status.get("units_done", 0), status["units_total"]
        lines.append(f"{done:,} of {total:,} units ({100 * done / total:.1f}%)")
    if outcome == "progress" and status and status.get("updated_at"):
        lines.append(f"Last update: {status['updated_at']}")
    if run_url:
        lines.append(run_url)
    return "\n".join(lines)[:DISCORD_MESSAGE_LIMIT]


def send(text, webhook_url):
    if not webhook_url:
        raise ValueError("DISCORD_WEBHOOK_URL must be set")
    response = requests.post(webhook_url, json={"content": text}, timeout=30)
    if response.status_code not in DISCORD_OK_STATUSES:
        raise RuntimeError(f"Discord answered with HTTP {response.status_code}")


def main(argv):
    parser = argparse.ArgumentParser(prog="python -m phase3.notify")
    parser.add_argument("--outcome", required=True, help="the scan step's outcome: success, failure, cancelled or skipped")
    parser.add_argument("--finished", default="false", choices=["true", "false"])
    parser.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    try:
        status = sync.read_status()
    except Exception:  # the message still goes out without the numbers
        status = None
    text = build_message(args.outcome, args.finished == "true", status, args.run_url)
    try:
        send(text, os.environ.get("DISCORD_WEBHOOK_URL", ""))
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"Could not send the Discord message ({type(error).__name__}).", file=sys.stderr)
        return 1
    print("Discord message sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
