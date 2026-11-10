"""Reads the scan's published files from the private phase3-state release, using a read-only token.

Kept Streamlit-free. The token is only ever sent to api.github.com.
"""

import requests

API = "https://api.github.com"
RELEASE_TAG = "phase3-state"
VIEW_NAME = "view.json"
STATUS_NAME = "status.json"


def fetch_asset(repo, token, name, session=None):
    http = session or requests.Session()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "airtel-phase3-live",
    }
    release = http.get(f"{API}/repos/{repo}/releases/tags/{RELEASE_TAG}", headers=headers, timeout=30)
    release.raise_for_status()
    matches = [asset for asset in release.json().get("assets", []) if asset["name"] == name]
    if not matches:
        raise LookupError(f"{name} is not in the {RELEASE_TAG} release yet")
    download = http.get(matches[0]["url"], headers={**headers, "Accept": "application/octet-stream"}, timeout=60)
    download.raise_for_status()
    return download.json()


def fetch_view(repo, token, session=None):
    return fetch_asset(repo, token, VIEW_NAME, session=session)
