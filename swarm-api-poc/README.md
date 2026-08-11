# swarm-api-poc — Raven (Wells Fargo) x Security Swarm

Working PoC for driving **Security Swarm ingestion-mode scans over the API**, in the
shape Wells Fargo's RAVEN controller would use: a Mythos tranche of AVITs comes in,
repo-scoped scans verify the findings against the code and consolidate them into
remediation groups, and the controller validates those groups before anything is fixed.

Living here because COG-GTM repo creation isn't available to Devin; this is GTM
working material, not part of the host repo's tooling.

## Start here
1. `README-POC.md` — the step-by-step walkthrough and what each step teaches.
2. `profile_v3.json` — the ingest-mode scan profile. This text is the product
   configuration: deterministic import, verify-against-code, and the grouping rules.
3. `SOLUTION-SUMMARY.md` — the full architecture write-up.
4. `raven-flow.png` — end-to-end diagram (Devin side vs. RAVEN side, both
   remediation paths).

## Scripts
| File | Role |
|------|------|
| `common.py` | API client, org/repo config, `state.json` chaining |
| `step1_upload_tranche.py` | tranche slice -> attachments API (the intake handoff) |
| `step2_create_profile.py` | create the ingest profile via `v3beta1` (no UI needed) |
| `step3_launch_scans.py` | one ingestion scan per repo; `--watch` to follow them |
| `step4_poll_findings.py` | poll findings, check AVIT accounting |
| `validate_groups.py` | deterministic group validator (completeness, size cap, file-overlap conflicts) |
| `planner_schema.json` | strict schema for structured grouping output |
| `group_findings.py` | baseline candidate partitioner — a scoring baseline, not the grouping policy |
| `mythos_tranche_poc.xlsx` | Mythos AVIT workbook: nodejs-goof (16) + meridian-insurance-crm (14) open AVITs |

Synthetic data: AVIT ids, app ids, and business units are fabricated; the
vulnerabilities, files, and line numbers are real (from Devin scans of COG-GTM repos).

## Auth
- `DEVIN_API_CODE_SCANS` — code-scans endpoints
- `DEVIN_API__KEY` — attachments upload (the code-scans key is not scoped for it)

## Scope
Covers intake -> ingestion -> triage/grouping. Remediation (per-finding sessions,
finding automations) and the scale/rate-limit contrast runs are deliberately out of
scope for now.
