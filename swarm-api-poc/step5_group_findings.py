"""Step 5 — deterministic controller-side grouping (RAVEN's job).

Pulls the scan's triaged findings, reads the GROUP-KEY line the triage
children stamped on each open finding (profile v6), and does a plain
GROUP BY into remediation batches. No AI here: the semantic judgment
(family, primary fix file, FP/duplicate) already happened per finding
inside the scan; this step only needs string parsing, so the grouping
is auditable and each AVIT lands in exactly one group by construction.

Prints the proposed batches and the remediation calls that WOULD be
made — it does not launch anything.

Run:  python3 step5_group_findings.py
"""

import re
from collections import defaultdict

from common import load_state, url, S

AVID_RE = re.compile(r"AVID-[0-9A-F]+")
KEY_RE = re.compile(r"^GROUP-KEY:\s*(.+)$", re.MULTILINE)
RAT_RE = re.compile(r"^GROUP-RATIONALE:\s*(.+)$", re.MULTILINE)

SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def fetch(scan_id):
    resp = S.get(url("code-scans/findings"),
                 params={"scan_id": scan_id, "limit": 200})
    resp.raise_for_status()
    return resp.json().get("items", [])


def group(findings):
    groups = defaultdict(list)
    missing = []
    dismissed = []
    for f in findings:
        note = f.get("note") or ""
        if f.get("status") == "dismissed":
            dismissed.append(f)
            continue
        m = KEY_RE.search(note)
        if not m:
            missing.append(f)
            continue
        groups[m.group(1).strip()].append(f)
    return groups, dismissed, missing


def show_finding(f):
    avits = sorted(set(AVID_RE.findall(str(f))))
    rat = RAT_RE.search(f.get("note") or "")
    line = f"      [{f['severity']:>8}] {f['title'][:70]}"
    line += f"\n                 AVITs: {', '.join(avits) or '?'}"
    if rat:
        line += f"\n                 rationale: {rat.group(1)[:110]}"
    return line


def main():
    state = load_state()
    for repo, scan_id in state.get("scans", {}).items():
        findings = fetch(scan_id)
        groups, dismissed, missing = group(findings)
        n_open = sum(len(v) for v in groups.values())
        print(f"\n=== {repo}  ({scan_id})")
        print(f"    findings: {len(findings)} total = "
              f"{n_open} open in {len(groups)} groups"
              f" + {len(dismissed)} dismissed + {len(missing)} MISSING KEY")

        for key, members in sorted(groups.items()):
            sev = min(members, key=lambda f: SEV_ORDER.get(f["severity"], 9))
            print(f"\n  GROUP {key}  ({len(members)} finding(s), "
                  f"severity {sev['severity']})")
            for f in members:
                print(show_finding(f))
            primary = sev
            print(f"    -> would remediate via primary finding "
                  f"{primary['finding_id']} (one session, one PR)")

        for f in dismissed:
            reason = (f.get("note") or "")[:90].replace("\n", " ")
            print(f"\n  DISMISSED: {f['title'][:60]} — {reason}")
        for f in missing:
            print(f"\n  !! OPEN WITHOUT GROUP-KEY (needs human review): "
                  f"{f['title'][:70]}")


if __name__ == "__main__":
    main()
