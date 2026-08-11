# Raven × Security Swarm — Where We've Landed

*Session artifacts: SWARM-API-AND-GROUPING.md (API + architecture detail),
CUSTOMER-GROUND-TRUTH.md (WF's workflow from the Aug 7 call), group_findings.py
(deterministic baseline candidate partitioner), planner_schema.json + validate_groups.py
(structured grouping contract and deterministic validator), raven_tranche_2026-08.sqlite
(synthetic test tranche).*

**Architecture correction (Aug 11):** final AVID-to-PR grouping is NOT a purely
deterministic operation. Deterministic code prepares and constrains the work
(intake, repo mapping, exact dedupe, size caps, accounting, rate control); a
code-aware AI step makes the semantic remediation-grouping decision (shared root
cause, coherent fix, predicted change set); deterministic validation and
conflict-aware scheduling wrap the AI output. The API is the outer control
plane; Security Swarm is the inner reasoning and execution plane.

---

## 1. The solution

### The problem in one sentence

WF's grouping (CWE family + same-file, 5–7 groups per repo) currently happens *inside* one
overloaded 20-AVID session, so it chunks **PRs** but not **session contexts** — and the
oversized contexts are what degrade fix quality (the Sonar-regression symptom).

### The architecture

Keep WF's controller; move it up one level. Instead of "launch a session with 20 AVIDs,"
it drives the code-scan API:

```
Mythos tranche arrives
  ↓  WF controller: resolve AVID → repo, commit tranche ONCE into wf/raven-tranches
  ↓                 (as a SQLite file — queried, never "read" by the model)
  ↓  per affected repo:   POST /v3/organizations/{org}/code-scans
  ↓                       {repo_name, profile_id: <Raven ingest profile>}
  ↓  the scan (ingest mode):
  ↓     • deterministic import: SELECT * FROM findings WHERE repo=... AND status='open'
  ↓     • verifies findings against the code (reachability, mitigations)
  ↓     • CODE-AWARE GROUPING: decides which AVIDs share a root cause and can be
  ↓       fixed coherently in one PR — scanner file_path and CWE are input features,
  ↓       not the grouping policy — emitting one finding per group with all AVIDs,
  ↓       root cause, and expected files-to-change
  ↓  poll GET .../code-scans/findings?scan_id=...   → the proposed groups, as records
  ↓  WF deterministic group validator: every AVID accounted for, no cross-repo
  ↓       groups, size cap respected, overlapping change sets flagged → serialize
  ↓       conflicting groups rather than merging unrelated fixes
  ↓  per approved finding: POST .../findings/{id}/remediate → one session → one PR
  └→ reconcile finding.pr_url + session_id + AVID list back into Raven
```

**Remediation mechanics (verified against Swarm source):** a scan session ends at
"consolidated findings persisted" and never writes fix PRs itself. Remediation is
always a separate top-level session per finding — not a child of the scan, not an
automation — launched by the remediate endpoint (or the UI "Fix" button), with a
purpose-built prompt from the finding + the profile's remediation guidance, and the
session_id claimed onto the finding (409 on doubles). Two ways to drive it:

- **Controller-in-the-loop (the pitch for WF):** poll findings → validate → remediate
  per finding. WF keeps full rate control, ordering, and the approval gate — what the
  diagram shows.
- **Finding Automations (hands-off, "later, once trusted"):** scan completion emits a
  `code_scan:finding` event per finding; an automation with a `remediate_finding`
  action auto-launches remediation, filterable by severity/repo/profile, default cap
  50 findings per scan per automation (UI: Security > Finding automations). The
  controller then only reconciles PR URLs back into Raven.

**The key mechanism — make the group be the finding.** Swarm's remediate endpoint is
structurally one finding → one session → one PR. So if the scan's triage phase emits each
approved group as a single consolidated finding, then group = finding = session = PR: all
four boundaries align. Grouping happens in a *different session* from code-writing, so no
remediation session ever sees more than its own group — small contexts are structural,
not a tuned cap. WF's "before it reaches Devin" means before the *remediation* Devin —
the grouping step itself may (and should) be AI, since "reported in the same file" is
scanner metadata while "fixing these requires changing the same file" is a prediction
about the remediation only a code-aware step can make.

**This is a hypothesis to validate, not a contract.** Profiles are guidance, not hard
constraints; agentic scans vary between runs. So: validate grouping output structurally
(planner_schema.json + validate_groups.py), don't promise exact grouping, and retain
fallbacks — (A) an external categorization session returning strict structured groups
that the controller fans out to direct remediation sessions, or (B) an external planner
writing pre-grouped records into the tranche store so ingestion imports each group as
one finding.

### What this preserves for WF

- Their grouping objectives (coherent review units, no conflicting concurrent PRs —
  with CWE family and same-file as input signals), now enforced at the session
  boundary, not just the PR boundary.
- Their controller, 429/backoff machinery, and structured-output reconciliation — the
  change is at the call-site (different endpoints), not in their operating model.
- AVID → PR traceability: each consolidated finding lists its AVIDs and returns
  `pr_url` + `session_id`.
- A capability they don't have today: chained attack paths (`related_finding_ids`).

### What we'd have prescribed on the call (with hindsight)

1. **"Your same-file rule needs a size cap — and it's really about merge conflicts."**
   Running a naive same-file merge against our 2,560-finding test tranche produced
   groups of 61–150 findings on hot files — silently worse than the 20-AVID contexts
   they already found degraded (`group_findings.py`, the baseline candidate
   partitioner, demonstrates this; its cap of 8 is an experimental pilot config, not
   WF ground truth). The refinement to confirm with WF: same-file co-location is a
   proxy for merge-conflict avoidance. Coherent same-root-cause fixes should merge;
   unrelated fixes with overlapping change sets should stay separate but be
   *serialized*, not forced into one mega-PR.
2. **"Don't chase a smaller cap; change where grouping happens."** The 50→20 experiment
   they ran is evidence for our architecture: context size drives quality. The fix isn't
   20→10, it's one-group-per-session.
3. **"You don't need Devin to reach Mythos."** The tranche-repo pattern: commit each
   tranche once into a dedicated repo in their SCM (which Devin already reaches to clone
   code); the profile filters to the repo under scan with SQL. No on-prem connectivity,
   no per-repo commits.
4. **"Yes, this is API-launchable — but scan creation is one repo per call."** Verified
   live: `POST /v3/organizations/{org}/code-scans` takes `{repo_name, profile_id, host,
   scan_type}` only. A tranche = a loop of POSTs, same shape as the session-create loop
   they run today. The UI's multi-repo/bulk scans are not on the API yet.
5. **"The grouping quality is the thing the pilot must measure."** Consolidation is
   profile prose, not a hard constraint — don't promise it, pilot it, and validate
   every run's output deterministically. (Hence the small capped test org they asked
   for is the right instinct.)

### Open questions we owe them answers on (for the product team)

1. How does a scan's internal fan-out (matcher/investigation/validation sub-sessions)
   count against the 500-concurrent-session cap? (Highest-stakes unknown — changes
   their capacity math.)
2. Per-scan findings upload in the create-scan API (would remove the tranche-repo
   workaround entirely — this is the "why can't we push it in like sessions" ask).
3. Multi-repo/bulk scan creation on the API, and its repo cap.
4. 429/backlog contract on scan-create and remediate.
5. Profile CRUD API (today profiles are UI-only, referenced by `profile_id`).
6. Any grouping/batch-remediate control beyond profile prose.

---

## 2. What we need to do to keep testing on our side

Everything below runs in COG-GTM against the synthetic tranche — no WF systems needed.

1. **Write the v3 ingest profile** (in the UI — no API). Grouping-centered, superseding
   PROFILE-v2: deterministic SQLite import + reconciliation counts, verify-against-code,
   code-aware grouping (shared root cause, coherent fix, predicted change set — with
   CWE/file_path as features and the size cap as a constraint), emit one finding per
   group carrying all AVIDs + root cause + expected files-to-change + rationale,
   remediation guidance per group. Output must conform to planner_schema.json.
2. **Set up the tranche repo**: create `COG-GTM/raven-tranches`, commit
   `raven_tranche_2026-08.sqlite`. (Optionally reshape the data first to WF's real
   wide-and-shallow shape: ≤20 AVIDs/repo with deliberate CWE/file overlap — the current
   tranche is lopsided, e.g. juice-shop 1,006.)
3. **Pilot scan #1 (smoke, cheap)**: POST a scan on `COG-GTM/nodejs-goof` (16 findings)
   with the v3 profile. Verifies the two load-bearing assumptions in one shot:
   - a scan session can clone the second (tranche) repo;
   - ingest mode consolidates into the groups we prescribed.
4. **Validate + score the grouping**: run `validate_groups.py` on the scan's emitted
   findings (AVID completeness, no cross-repo groups, size cap, no unexplained
   duplicate membership, conflicts explicit), then compare against
   `group_findings.py`'s baseline — where and why the code-aware grouping deviates
   from the file/CWE baseline is the signal, not disagreement per se. Also check
   stability across a repeated run.
5. **Remediate one group end-to-end**: one `remediate` call → confirm one session, one
   PR covering all AVIDs in the group, `pr_url`/`session_id` on the finding.
6. **Pilot scan #2 (the hard case)**: a hot-file repo (juice-shop shard) to prove the
   size-cap split works and contexts stay small.
7. **Concurrency probe**: while a scan runs, count its child sessions (org session list)
   to answer question #1 above empirically before product does.
8. **Pilot the fallback path too**: an external categorization session that returns
   planner_schema.json groups, with the controller launching direct remediation
   sessions per group — compare both paths on grouping quality, AVID traceability,
   session/PR count, Sonar regressions, ACU cost, and 429 behavior.
9. **Then the WF-facing demo**: controller script that does the full loop —
   tranche → scans → poll → validate → remediate → AVID→PR report — as the thing we
   show Raven.

Blocking approvals needed from you: step 1 (I can drive the UI or hand you the text),
and go-ahead on steps 3+ since scans consume ACUs.

Housekeeping: two junk scans from API probing (`does-not-exist-zzz`, `x/y`) still need
archiving in the UI.
