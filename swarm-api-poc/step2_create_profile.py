"""STEP 2 — Create the ingest-mode scan profile (API, v3beta1).

What this simulates: the one-time setup RAVEN would do. The profile is the
contract for every scan: where findings come from (ingestion_source_guidance),
how to verify and group them (post_ingestion_guidance), what the report looks
like, and the rules a remediation session must follow.

Read profile_v3.json first — that text IS the product configuration.

Run:  python3 step2_create_profile.py
"""

import json
import os

from common import S, load_state, save_state, show, url

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "profile_v3.json")) as f:
    profile = json.load(f)

print(f"Creating profile: {profile['name']}")
resp = S.post(url("code-scans/profiles", beta=True), json=profile)
body = show(resp)
resp.raise_for_status()

profile_id = body.get("profile_id")
state = load_state()
state["profile_id"] = profile_id
save_state(state)
print(f"\nProfile created: {profile_id}")
print("It is now visible in the UI under Security > Profiles.")
print("Next: step3_launch_scans.py")
