"""Sends a WhatsApp message through CallMeBot when a scan run pauses, completes or fails.

Settings come from the environment: WHATSAPP_PHONE (country code and digits, e.g. 917700000000) and
CALLMEBOT_APIKEY. The key is never printed: a failed send reports only the error type.
"""

import argparse
import os
import sys

import requests

from phase3 import sync

CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"


def build_message(outcome, finished, status, run_url):
    if outcome == "success" and finished:
        heading = "Phase 3 scan COMPLETE"
    elif outcome == "success":
        heading = "Phase 3 scan paused at its time limit; the next run starts automatically"
    else:
        heading = f"Phase 3 scan STOPPED with an error ({outcome})"
    lines = [heading]
    if status and status.get("units_total"):
        done, total = status.get("units_done", 0), status["units_total"]
        lines.append(f"{done:,} of {total:,} units ({100 * done / total:.1f}%)")
    lines.append(run_url)
    return "\n".join(lines)


def send(text, phone, apikey):
    if not phone or not apikey:
        raise ValueError("WHATSAPP_PHONE and CALLMEBOT_APIKEY must both be set")
    response = requests.get(CALLMEBOT_URL, params={"phone": phone, "text": text, "apikey": apikey}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"CallMeBot answered with HTTP {response.status_code}")


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
        send(text, os.environ.get("WHATSAPP_PHONE", ""), os.environ.get("CALLMEBOT_APIKEY", ""))
    except (requests.RequestException, RuntimeError, ValueError) as error:
        # Print the type only: a request error can contain the key in its URL.
        print(f"Could not send the WhatsApp message ({type(error).__name__}).", file=sys.stderr)
        return 1
    print("WhatsApp message sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
