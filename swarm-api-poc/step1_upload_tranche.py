"""STEP 1 — Intake: prepare and upload the Mythos tranche.

What this simulates: RAVEN's controller receiving a Mythos tranche and making
it available to Devin. No tranche repo, no on-prem connectivity — the file is
uploaded once via the attachments API and referenced by the ingestion scans.

What it does:
  1. Slices the master tranche (raven_tranche_2026-08.sqlite) down to just the
     PoC repos — deterministic intake: open findings only, exact rows preserved.
  2. Uploads the slice via the attachments API.
  3. Records the attachment URL in state.json for step 3.

Run:  python3 step1_upload_tranche.py
"""

import os
import sqlite3

from common import ATTACH, BASE, REPOS, load_state, save_state

HERE = os.path.dirname(os.path.abspath(__file__))
# The full 2,560-finding tranche is not committed (10.9 MB); the pre-built
# 2-repo slice is. Set MYTHOS_TRANCHE to a master tranche to rebuild the slice.
MASTER = os.environ.get("MYTHOS_TRANCHE")
SLICE = os.path.join(HERE, "raven_tranche_poc.sqlite")


def build_slice():
    if os.path.exists(SLICE):
        os.remove(SLICE)
    src = sqlite3.connect(MASTER)
    dst = sqlite3.connect(SLICE)
    src.row_factory = sqlite3.Row

    cols = [r[1] for r in src.execute("PRAGMA table_info(findings)")]
    dst.execute(f"CREATE TABLE findings ({', '.join(c + ' TEXT' for c in cols)})")
    placeholders = ",".join("?" * len(cols))
    total = 0
    for repo in REPOS:
        rows = src.execute(
            "SELECT * FROM findings WHERE repo = ? AND status = 'open'", (repo,)
        ).fetchall()
        dst.executemany(f"INSERT INTO findings VALUES ({placeholders})",
                        [tuple(r) for r in rows])
        print(f"  {repo}: {len(rows)} open AVITs")
        total += len(rows)
    dst.execute("CREATE INDEX idx_repo ON findings(repo)")
    dst.execute("CREATE INDEX idx_file ON findings(repo, file_path)")
    dst.commit()
    dst.close()
    src.close()
    print(f"  slice written: {SLICE} ({total} rows, {os.path.getsize(SLICE)//1024} KB)")


def upload():
    with open(SLICE, "rb") as f:
        resp = ATTACH.post(f"{BASE}/v1/attachments",
                      files={"file": ("raven_tranche_poc.sqlite", f,
                                      "application/octet-stream")})
    print(f"  upload -> HTTP {resp.status_code}")
    resp.raise_for_status()
    # endpoint returns the attachment URL (string or json)
    try:
        body = resp.json()
        url = body if isinstance(body, str) else body.get("url") or body.get("attachment_url")
    except ValueError:
        url = resp.text.strip().strip('"')
    print(f"  attachment URL: {url}")
    return url


if __name__ == "__main__":
    if MASTER:
        print("[1/2] Building deterministic tranche slice (RAVEN-side intake)")
        build_slice()
    else:
        print("[1/2] Using committed slice (set MYTHOS_TRANCHE to rebuild it)")
    print("\n[2/2] Uploading via attachments API (the 'push it in' step)")
    url = upload()
    state = load_state()
    state["tranche_attachment_url"] = url
    save_state(state)
    print("\nDone. Next: step2_create_profile.py")
