# Raven x Security Swarm PoC — Intake / Ingestion / Triage

Goal: understand, hands-on, the part of Swarm that is new to you — **ingest-mode
scans**: how Mythos findings get in, how a scan verifies them against code, and
how triage consolidates them into remediation groups. We stop before
remediation (that half you already know from classic Devin sessions).

Everything runs from this directory with `python3 stepN_*.py`, using the
`DEVIN_API_CODE_SCANS` key against the devin-gtm org. State accumulates in
`state.json` so steps chain.

## The story each step tells

| Step | You are playing | What happens | Swarm concept it teaches |
|------|-----------------|--------------|--------------------------|
| 1 | RAVEN intake | Slice the master tranche to 2 repos (nodejs-goof: 16 AVITs, meridian-insurance-crm: 14), upload once via the attachments API | Tranche handoff without a tranche repo or DB connectivity |
| 2 | RAVEN one-time setup | Create the ingest-mode profile via API (read `profile_v3.json` first) | The profile is the contract: `ingestion_source_guidance` (deterministic import), `post_ingestion_guidance` (verify + group), report + remediation rules |
| 3 | RAVEN controller | Create 2 ingestion scans referencing the same attachment; `--watch` them run in parallel | Scan creates are queued and drained by a dispatcher — no 429 storms; repo-scoped scans parallelize naturally |
| 4 | RAVEN reconciliation | Poll GET findings; check AVIT accounting against the tranche | Consolidated findings = proposed remediation groups; the controller's deterministic validation gate before any remediation |

While step 3 runs, watch the same scans in the UI (Security > Scans): threat
model, worker fan-out, findings arriving live. That's the demo view; the
scripts are the controller view. Same objects, two lenses.

## What "good" looks like at step 4
- Every open AVIT accounted for exactly once (grouped, dismissed with
  file:line evidence, or flagged needs-human-review).
- Groups that make code sense: same root cause, one coherent fix, expected
  files-to-change stated — not "same file so same PR".
- Any two groups predicted to touch the same files flagged as conflicts
  (controller would serialize them, not merge them).

## Terminology mapping (Swarm docs -> RAVEN language)
- scan profile = the reusable contract for a tranche type
- ingest mode = "start from Mythos findings, do not discover new ones"
- consolidated finding = one AVIT remediation group
- Assign to Devin / remediate endpoint = launch one focused remediation
  session for one group (NOT in scope for this PoC)
- Finding Automation = optional hands-off trigger for the same launcher

## Not in this PoC (deliberately)
- Remediation (step 5 later: one manual remediate call, then automations)
- The 429 contrast blast (step 6 later, for the demo)
- juice-shop scale test (1,006 findings, hot files)
