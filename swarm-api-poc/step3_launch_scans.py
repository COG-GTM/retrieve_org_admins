"""STEP 3 — Launch the two ingestion scans (parallelization in action).

What this simulates: RAVEN's controller creating one repo-scoped scan per
repository in the tranche. Both scans reference the same tranche attachment;
each scan's profile tells it to import only its own repo's open AVITs.

Key contrast with today: scan creates are queue-based (per-org backlog,
drained by a dispatcher) — they do not 429 like session creates. Pacing is
against a backlog cap, not a concurrency storm.

Run:  python3 step3_launch_scans.py           # create both scans
      python3 step3_launch_scans.py --watch    # poll status until done
"""

import sys
import time

from common import REPOS, S, load_state, save_state, show, url

state = load_state()


def create():
    profile_id = state["profile_id"]
    attachment = state["tranche_attachment_url"]
    scans = state.setdefault("scans", {})
    for repo in REPOS:
        print(f"\nCreating ingestion scan for {repo}")
        resp = S.post(
            url("code-scans/ingestion"),
            json={
                "repo_name": repo,
                "profile_id": profile_id,
                "attachment_urls": [attachment],
            },
        )
        body = show(resp)
        if resp.ok and body:
            scans[repo] = body.get("scan_id") or body.get("id")
    save_state(state)
    print("\nScans queued. Watch them drain: python3 step3_launch_scans.py --watch")
    print("Also visible live in the UI: Security > Scans")


def watch():
    scans = state.get("scans", {})
    while True:
        statuses = {}
        for repo, scan_id in scans.items():
            resp = S.get(url(f"code-scans/{scan_id}"))
            body = resp.json() if resp.ok else {}
            statuses[repo] = body.get("status", f"HTTP {resp.status_code}")
        line = " | ".join(f"{r.split('/')[-1]}: {s}" for r, s in statuses.items())
        print(time.strftime("%H:%M:%S"), line)
        if all(s in ("completed", "failed", "cancelled") for s in statuses.values()):
            break
        time.sleep(60)


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    else:
        create()
