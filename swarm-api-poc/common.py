"""Shared client for the Raven x Security Swarm PoC.

Auth: DEVIN_API_CODE_SCANS for the code-scans endpoints, DEVIN_API__KEY for the
attachments upload (the code-scans key is not scoped for attachments).
State (attachment URLs, profile_id, scan_ids) accumulates in poc/state.json
so each step picks up where the previous one left off.
"""

import json
import os
import sys

import requests

BASE = "https://api.devin.ai"
ORG = "org_69IXJFLrljx8zSAw"  # devin-gtm
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

REPOS = ["COG-GTM/nodejs-goof", "COG-GTM/meridian-insurance-crm"]

TOKEN = os.environ.get("DEVIN_API_CODE_SCANS")
if not TOKEN:
    sys.exit("DEVIN_API_CODE_SCANS is not set")

S = requests.Session()
S.headers["Authorization"] = f"Bearer {TOKEN}"

# separate session for the attachments API (different key scope)
ATTACH = requests.Session()
ATTACH.headers["Authorization"] = f"Bearer {os.environ.get('DEVIN_API__KEY', '')}"


def url(path, beta=False):
    ver = "v3beta1" if beta else "v3"
    return f"{BASE}/{ver}/organizations/{ORG}/{path}"


def show(resp):
    print(f"  -> HTTP {resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(body, indent=2)[:3000])
        return body
    except ValueError:
        print(resp.text[:1000])
        return None


def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    print(f"\n[state saved to {STATE_PATH}]")
