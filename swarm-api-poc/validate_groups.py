"""Deterministic validator for AI grouping output (planner_schema.json).

Run by the controller between the code-aware grouping step and any remediation
launch. Enforces the rules that are rules, not judgment calls:

  V1  output conforms to planner_schema.json
  V2  every input AVID is accounted for exactly once
      (a group, ungrouped_avids, or duplicates)
  V3  no cross-repository groups (all AVIDs belong to plan.repository)
  V4  group size within the configured cap
  V5  no unexplained duplicate membership across groups
  V6  duplicate targets resolve to a real grouped/ungrouped AVID
  V7  overlapping expected change sets across groups are flagged as
      conflicts -> serialize, never merge unrelated fixes

Exit nonzero (or raise) on any hard violation; conflicts are returned for
scheduling, not treated as errors.

Usage:
    python3 validate_groups.py plan.json --db raven_tranche_2026-08.sqlite
    (the DB provides the expected AVID set for the plan's repository)
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "raven_tranche_poc.sqlite")
SCHEMA_PATH = os.path.join(HERE, "planner_schema.json")
MAX_GROUP_SIZE = 8  # experimental pilot configuration, not WF ground truth


def load_expected_avids(db_path, repo):
    con = sqlite3.connect(db_path)
    avids = {r[0] for r in con.execute(
        "SELECT avid FROM findings WHERE repo = ? AND status = 'open'", (repo,))}
    con.close()
    return avids


def validate(plan, expected_avids, max_group_size=MAX_GROUP_SIZE):
    """Returns (errors, conflicts). errors -> reject the plan;
    conflicts -> groups whose expected change sets overlap (serialize them)."""
    errors = []

    # V1: schema (structural; use jsonschema if available, else minimal checks)
    try:
        import jsonschema
        with open(SCHEMA_PATH) as f:
            jsonschema.validate(plan, json.load(f))
    except ImportError:
        for key in ("repository", "groups", "ungrouped_avids", "duplicates"):
            if key not in plan:
                errors.append(f"V1 missing top-level key: {key}")
        for g in plan.get("groups", []):
            for key in ("group_id", "avids", "shared_root_cause",
                        "expected_files_to_change", "grouping_rationale",
                        "confidence"):
                if key not in g:
                    errors.append(f"V1 group {g.get('group_id','?')} missing: {key}")
    except Exception as e:  # jsonschema.ValidationError
        errors.append(f"V1 schema violation: {e}")

    groups = plan.get("groups", [])
    grouped = [a for g in groups for a in g.get("avids", [])]
    ungrouped = plan.get("ungrouped_avids", [])
    dupes = plan.get("duplicates", [])
    dupe_avids = [d["avid"] for d in dupes]

    # V5: no AVID in more than one group / bucket
    counts = Counter(grouped + ungrouped + dupe_avids)
    for avid, n in counts.items():
        if n > 1:
            errors.append(f"V5 AVID {avid} appears {n} times across groups/buckets")

    # V2: every expected AVID accounted for, nothing invented
    seen = set(counts)
    missing = expected_avids - seen
    unknown = seen - expected_avids
    if missing:
        errors.append(f"V2 unaccounted AVIDs ({len(missing)}): {sorted(missing)[:10]}")
    if unknown:
        errors.append(f"V2/V3 AVIDs not in this repo's open set ({len(unknown)}): "
                      f"{sorted(unknown)[:10]}")

    # V4: size cap
    for g in groups:
        if len(g.get("avids", [])) > max_group_size:
            errors.append(f"V4 group {g['group_id']} has {len(g['avids'])} AVIDs "
                          f"(cap {max_group_size})")

    # V6: duplicate targets resolve
    resolvable = set(grouped) | set(ungrouped)
    for d in dupes:
        if d.get("duplicate_of") not in resolvable:
            errors.append(f"V6 duplicate {d.get('avid')} points at "
                          f"{d.get('duplicate_of')}, which is not a grouped/ungrouped AVID")

    # V7: conflict detection on expected change sets (flag, don't fail)
    by_file = defaultdict(list)
    for g in groups:
        for f in g.get("expected_files_to_change", []):
            by_file[f].append(g["group_id"])
    conflicts = sorted(
        {tuple(sorted(pair))
         for gids in by_file.values() if len(gids) > 1
         for i, a in enumerate(gids) for pair in [(a, b) for b in gids[i + 1:]]}
    )

    return errors, conflicts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="planner output JSON file")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--max-group-size", type=int, default=MAX_GROUP_SIZE)
    args = ap.parse_args()

    with open(args.plan) as f:
        plan = json.load(f)
    expected = load_expected_avids(args.db, plan.get("repository", ""))
    errors, conflicts = validate(plan, expected, args.max_group_size)

    if conflicts:
        print(f"{len(conflicts)} group pair(s) share expected files -> serialize:")
        for a, b in conflicts:
            print(f"  {a} <-> {b}")
    if errors:
        print(f"\nREJECTED — {len(errors)} violation(s):")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    print(f"\nOK: {len(plan['groups'])} groups, {len(expected)} AVIDs accounted for, "
          f"{len(plan.get('duplicates', []))} duplicates, "
          f"{len(plan.get('ungrouped_avids', []))} ungrouped")


if __name__ == "__main__":
    main()
