"""STEP 1 — Intake: upload the Mythos AVIT workbook.

What this simulates: RAVEN's controller receiving a Mythos tranche and making it
available to Devin. This is the same move RAVEN already makes today — upload a
file via the attachments API and reference it on the create call. Nothing about
their data handoff has to change: no on-prem DB connectivity, no tranche repo,
no export pipeline. Only the second call differs (ingestion scan, not session).

What it does:
  1. Slices the master Mythos workbook down to the PoC repos, open AVITs only
     (deterministic, RAVEN-side filtering — the equivalent of the avit_table
     they build per group today).
  2. Uploads the slice via POST /v1/attachments.
  3. Records the attachment URL in state.json for step 3.

Run:  python3 step1_upload_tranche.py
"""

import os

from common import ATTACH, BASE, REPOS, load_state, save_state

HERE = os.path.dirname(os.path.abspath(__file__))
SLICE = os.path.join(HERE, "mythos_tranche_poc.xlsx")
# The full 2,560-row Mythos workbook is not committed (2.8 MB); the 2-repo
# slice is. Set MYTHOS_WORKBOOK to a master export to rebuild the slice.
MASTER = os.environ.get("MYTHOS_WORKBOOK")


def build_slice():
    import pandas as pd

    df = pd.read_excel(MASTER)
    sub = df[df["repo"].isin(REPOS) & (df["status"] == "open")]
    sub.to_excel(SLICE, index=False)
    for repo, n in sub.groupby("repo").size().items():
        print(f"  {repo}: {n} open AVITs")
    print(f"  slice written: {SLICE} ({len(sub)} rows, "
          f"{os.path.getsize(SLICE)//1024} KB)")


def upload():
    name = os.path.basename(SLICE)
    with open(SLICE, "rb") as f:
        resp = ATTACH.post(
            f"{BASE}/v1/attachments",
            files={"file": (name, f,
                            "application/vnd.openxmlformats-officedocument"
                            ".spreadsheetml.sheet")},
        )
    print(f"  upload -> HTTP {resp.status_code}")
    resp.raise_for_status()
    try:
        body = resp.json()
        url = body if isinstance(body, str) else body.get("url") or body.get("attachment_url")
    except ValueError:
        url = resp.text.strip().strip('"')
    print(f"  attachment URL: {url}")
    return url


if __name__ == "__main__":
    if MASTER:
        print("[1/2] Building the AVIT workbook slice (RAVEN-side intake)")
        build_slice()
    else:
        print("[1/2] Using the committed workbook slice "
              "(set MYTHOS_WORKBOOK to rebuild it)")
    print("\n[2/2] Uploading via the attachments API — the same call RAVEN "
          "already makes to attach files to a session")
    url = upload()
    state = load_state()
    state["tranche_attachment_url"] = url
    save_state(state)
    print("\nDone. Next: step2_create_profile.py")
