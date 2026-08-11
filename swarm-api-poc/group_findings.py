"""Baseline candidate partitioner for Raven/Mythos tranches.

This is NOT the definitive implementation of Wells Fargo's grouping policy.
Scanner-reported file_path and CWE are metadata, not a prediction of which
files a fix will actually change — that semantic grouping decision requires a
code-aware step (Swarm triage or an external planning session). This script is
useful as:
  * a deterministic candidate pre-partitioner (bounded batches into the planner);
  * a baseline against which AI grouping output is scored;
  * a hot-file stress test (single files here carry up to 150 findings);
  * a check of AVID completeness and size-cap behavior.

Heuristics encoded (from WF's Aug 7 description, as signals not policy):
  H1  partition by CWE ID / CWE family
  H2  co-locate findings reported against the same file (their stated aim was
      merge-conflict avoidance, so serialization of conflicting groups may
      satisfy it without forcing one PR — to be confirmed with WF)
  H3  cap batch size so one Devin session can hold it

An oversized file batch is split by CWE first, then contiguous line-range
chunks. MAX_PER_GROUP = 8 is an experimental pilot configuration, not WF
ground truth.

Output is one row per candidate batch. Batches are input to the code-aware
grouping step, not the final remediation contract.
"""

import os
import sqlite3
import sys
from collections import defaultdict

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raven_tranche_poc.sqlite")
MAX_PER_GROUP = 8


def candidate_batches(db_path=DB, max_per_group=MAX_PER_GROUP):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT finding_id, avid, repo, file_path, cwe, severity,
                  CAST(start_line AS INTEGER) AS start_line
           FROM findings WHERE status = 'open'
           ORDER BY repo, file_path, cwe, start_line"""
    ).fetchall()
    con.close()

    by_file = defaultdict(list)
    for r in rows:
        by_file[(r["repo"], r["file_path"])].append(r)

    batches = []
    for (repo, file_path), items in by_file.items():
        if len(items) <= max_per_group:
            batches.append(_batch(repo, file_path, items))
            continue
        # H3 split: by CWE first, then by contiguous line ranges.
        by_cwe = defaultdict(list)
        for r in items:
            by_cwe[r["cwe"]].append(r)
        for cwe_items in by_cwe.values():
            cwe_items.sort(key=lambda r: r["start_line"])
            for i in range(0, len(cwe_items), max_per_group):
                batches.append(_batch(repo, file_path, cwe_items[i : i + max_per_group]))
    return batches


# Back-compat alias
group = candidate_batches


def _batch(repo, file_path, items):
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    cwes = sorted({r["cwe"] for r in items})
    lines = sorted(r["start_line"] for r in items)
    return {
        "repo": repo,
        "file_path": file_path,
        "cwes": cwes,
        "line_range": (lines[0], lines[-1]),
        "max_severity": min((r["severity"] for r in items), key=lambda s: sev_rank.get(s, 9)),
        "finding_ids": [r["finding_id"] for r in items],
        "avids": [r["avid"] for r in items],
        "title": f"{'/'.join(cwes)} in {file_path} (lines {lines[0]}-{lines[-1]})",
    }


if __name__ == "__main__":
    batches = candidate_batches()
    sizes = [len(b["avids"]) for b in batches]
    per_repo = defaultdict(lambda: [0, 0])
    for b in batches:
        per_repo[b["repo"]][0] += len(b["avids"])
        per_repo[b["repo"]][1] += 1

    print(f"{sum(sizes)} findings -> {len(batches)} candidate batches "
          f"(max {max(sizes)}, mean {sum(sizes)/len(batches):.1f})\n")
    print("  %-55s %7s %7s %11s" % ("repo", "avids", "batches", "avids/batch"))
    for repo, (f, u) in sorted(per_repo.items(), key=lambda kv: -kv[1][0]):
        print("  %-55s %7d %7d %11.1f" % (repo, f, u, f / u))
    multi = [b for b in batches if len(b["avids"]) > 1]
    mixed = [b for b in batches if len(b["cwes"]) > 1]
    print(f"\n{len(multi)} batches hold 2+ AVIDs; {len(mixed)} hold mixed CWEs "
          f"in one file (H2 overriding H1)")
    if "-v" in sys.argv:
        for b in sorted(batches, key=lambda b: -len(b["avids"]))[:10]:
            print(f"\n  {b['title']}\n    {len(b['avids'])} AVIDs: {', '.join(b['avids'][:6])}...")
