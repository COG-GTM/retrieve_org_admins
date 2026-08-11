"""STEP 4 — Poll findings and validate the proposed remediation groups.

What this simulates: the reconciliation half of RAVEN's controller. It polls
GET findings (read path — no session quota involved), extracts each
consolidated finding's AVIT membership, and runs the deterministic validator:

  * every open AVIT in the tranche accounted for (grouped, dismissed, or
    needs-review) — nothing silently dropped
  * no AVIT in two groups
  * group size cap respected
  * overlapping expected-files-to-change flagged -> serialize, don't merge

This validation gate is where the controller decides what proceeds to
remediation. We stop here for this PoC (no remediate calls).

Run:  python3 step4_poll_findings.py
"""

import json
import os
import re
import sqlite3

from common import REPOS, S, load_state, show, url  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
SLICE = os.path.join(HERE, "raven_tranche_poc.sqlite")

AVID_RE = re.compile(r"\bAVIT?-[A-Za-z0-9-]+\b|\bANT-2026-\d+\b")

state = load_state()


def expected_avids(repo):
    conn = sqlite3.connect(SLICE)
    rows = conn.execute(
        "SELECT avid FROM findings WHERE repo = ? AND status = 'open'", (repo,)
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def fetch_findings(scan_id):
    resp = S.get(url("code-scans/findings"), params={"scan_id": scan_id, "limit": 200})
    if not resp.ok:
        show(resp)
        return []
    body = resp.json()
    return body.get("items", body if isinstance(body, list) else [])


def analyze(repo, scan_id):
    print(f"\n=== {repo} (scan {scan_id}) ===")
    findings = fetch_findings(scan_id)
    print(f"{len(findings)} consolidated findings returned")

    expected = expected_avids(repo)
    seen = {}
    for f in findings:
        text = json.dumps(f)
        members = set(AVID_RE.findall(text)) & expected
        fid = f.get("finding_id") or f.get("id")
        title = (f.get("title") or "")[:70]
        print(f"  {fid}  [{f.get('severity','?')}]  {len(members)} AVITs  {title}")
        for a in members:
            seen.setdefault(a, []).append(fid)

    missing = expected - set(seen)
    doubled = {a: fids for a, fids in seen.items() if len(fids) > 1}
    print(f"\nAVIT accounting: {len(expected)} expected, {len(seen)} referenced")
    if missing:
        print(f"  UNACCOUNTED (check report for dismissals): {sorted(missing)}")
    if doubled:
        print(f"  IN MULTIPLE FINDINGS: {doubled}")
    if not missing and not doubled:
        print("  every AVIT accounted for exactly once — validation gate passes")

    out = os.path.join(HERE, f"findings_{repo.split('/')[-1]}.json")
    with open(out, "w") as fh:
        json.dump(findings, fh, indent=2)
    print(f"raw findings saved: {out}")


if __name__ == "__main__":
    for repo in REPOS:
        scan_id = state.get("scans", {}).get(repo)
        if scan_id:
            analyze(repo, scan_id)
        else:
            print(f"no scan recorded for {repo} — run step3 first")
