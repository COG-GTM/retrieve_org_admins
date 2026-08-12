# RAVEN × Security Swarm — Full Context Summary

Everything gathered across this working session (and its predecessors) on the Wells Fargo
RAVEN use case: what they run today, why they want to improve it, what we built and
empirically learned, and the recommended architecture.

---

## 1. Who / what is RAVEN

RAVEN is Wells Fargo's fleet-level remediation controller. It sits between **Mythos**
(their internal LLM-based security scanner) and Devin:

- Mythos produces **AVIT** records (their finding unit; each has an AVID identifier,
  CWE ID, severity, file/line, description, remediation suggestion).
- RAVEN batches AVITs into **tranches** (a tranche can span ~hundreds of repos) and
  pushes them to Devin over the v3 API: it uploads a file of AVITs via
  `POST /v1/attachments`, then creates a session referencing that attachment.
- Each session's prompt: "Fix the vulnerabilities in the AVIT records" + a `group_id`
  + an inline `avit_table`, an enterprise playbook, and ~13 baked skills, including
  `avit-input-parsing`, `fp-classification`, and `devin-mediated-grouping`.
- RAVEN operates under a **500 concurrent session** quota on its `bot_apk` API identity.

## 2. Their current flow and its problems

Today, ONE Devin session receives up to ~20 AVITs and does everything: parse the table,
classify false positives, group the AVITs, and write the fixes.

Grouping instructions they give Devin today (their words, from the call):

> "everything would go in one PR and then people came back with the feedback that …
> review becomes too tough because you're clubbing all the unrelated habits"

> "what we tell it is that look at the CWE ID, the CWE family … we say that feel free
> to break it up into five or seven groups."

> "apart from CWID, we also tell it that if multiple events need change in the same
> file that also you group together because otherwise it would be like three PRs on
> the same file and merge issues and all that."

> "if you have to reduce that, but yet retain the grouping logic which makes review
> easier. Then we'll have to do that grouping before even reaching Devin."

So the target review shape is: **~5–7 groups per ~20 AVITs, grouped by CWE family,
with a hard same-file merge rule**, one PR per group.

Pain points motivating the change:

1. **Oversized session context** — one session carries all ~20 AVITs + repo + parsing +
   FP-classification + grouping + fixing. Quality degrades; reasoning is trapped in a
   transcript.
2. **Unrelated/conflicting PRs** — grouping done inside the same session that fixes;
   overlapping PRs touch the same files and cause merge conflicts.
3. **Review backlog** — application teams must review PRs that mix unrelated issues, or
   too many PRs.
4. **False positives cost engineering time** — Mythos is an LLM scanner; noise flows
   straight into fixes and review.
5. **No durable, queryable intermediate state** — reconciliation to AVITs is by parsing
   PR text; nothing persists between "AVITs in" and "PRs out".
6. **Session-quota pressure** — everything runs on the bot_apk 500-session pool.

## 3. The proposed architecture

```
Mythos AVIT tranche (xlsx)
  → RAVEN uploads file:            POST /v1/attachments
  → one ingestion scan per repo:   POST /v3/organizations/{org}/code-scans/ingestion
        { repo_name, profile_id, attachment_urls }
  → scan: import → verify each AVIT vs real code (FP gate) → triage
        (dedupe, GROUP-KEY, consolidate-to-survivor)
  → durable findings (one per remediation group, member AVITs listed)
  → RAVEN validates accounting (deterministic: validate_groups.py)
  → one remediation per group:     POST .../findings/{id}/remediate
  → PR per group; session_id + pr_url stamped back on the finding
  → RAVEN reconciles by AVID
```

RAVEN stays the fleet control plane; Swarm becomes the repo-scoped verification +
triage + durable-findings layer. Remediation sessions stay "vanilla Devin" — just
better-scoped, pre-seeded with verified evidence, and auto-tracked.

## 4. Vocabulary (docs-verified)

- **Profile** — reusable scan policy: scope + per-stage guidance. Like a playbook, but
  the output is findings, not a PR. Parameterized at call time by `repo_name` +
  `attachment_urls`. One profile serves the whole fleet.
- **Scan** — one run: one repo × one profile, executed by parallel Devins
  ("Agentic MapReduce").
- **Mode** — exactly two: `discover` (Swarm finds vulns itself, builds a threat model)
  and `ingest` (starts from supplied external findings). Independent of `scan_type`
  (only `security` is enabled in this org; 9 other types exist behind feature flags).
- **Threat model** — per-run, repo-specific analysis (discover mode only). Not a profile.
- **Finding** — the durable deliverable of a scan: severity, status
  (Open/Reviewed/Dismissed), confidence, category, evidence, recommendation, and (after
  remediation) session_id + pr_url.
- **Remediation** — an action on a finding (UI "Assign to Devin", API remediate call, or
  automation). Launches an ordinary Devin session; NOT another scan.

## 5. What we verified from source (via source-code tracing)

- **Ingest mode is purpose-built** for this: "the customer already has findings from
  their own tooling, and this scan imports them so they can be managed here"
  (ingest_orchestrator). Pipeline: ingest child (reads attachments, creates findings)
  → optional interactive pause → parallel triage children (per-finding dispositions:
  dismiss / adjust severity / keep, driven by profile guidance) → optional
  validation/report.
- **No aggregation phase in ingest mode**: nothing merges findings into new group
  objects. Consolidation must be expressed as dispositions (dismiss-into-survivor) —
  which is what profile v7 does.
- **Findings carry a group-by field**: the ingest tool accepts per-finding `vuln_slug` /
  `category` / `external_id` (the AVID), settable at import via ingestion guidance;
  `category` is exposed on the v3 findings API for a client-side groupby. Category
  cannot be changed later in triage (notes can).
- **Quota identities** (per (user_id, org_id), verified code path):
  - Scan orchestrators + ALL worker children → `CodeScanService`: concurrent **5,000**/org.
    Scan fan-out does NOT consume RAVEN's 500.
  - Automation-triggered sessions → `automationservice`: **1,000**.
  - Direct API calls as WF's key (`bot_apk`) → their **500** pool (remediate calls included).
- **Finding Automations** trigger per finding (would fire ~13 sessions, not 5-7); for
  grouped remediation the trigger belongs in RAVEN's controller. No code-scan-finding
  event type exists in the general automations engine today.

## 6. Empirical results (COG-GTM/nodejs-goof: 16 AVITs, COG-GTM/meridian-insurance-crm: 14 AVITs)

| Run | Grouping policy | Result | Lesson |
|---|---|---|---|
| v3 (goof) | grouping in post_ingestion_guidance | 16 findings / 16 AVITs, 1:1 | grouping guidance belongs in triage_guidance |
| v4 | root-cause-purity grouping | goof 1:1 (reasoned); meridian 14 findings but **duplicate AVIT membership** (20 refs / 14 AVITs) | AI grouping needs exact-once accounting enforced |
| v5 | WF's policy verbatim (CWE family + same-file + 5-7 target) | meridian 13 open + 1 duplicate dismissed, zero dup membership — but **no consolidation** (1 AVIT per finding); goof dismissed a real FP (SSTI neutralized by Dust auto-escaping, evidence to compiler source) | per-finding disposition mechanism can't merge; FP gate genuinely works |
| v6 | GROUP-KEY tagging + controller groupby (step5) | meridian 12 open in **7 groups** + 2 dismissed; goof 16 open in 12 groups; 0 missing keys, exact-once by construction | AI classifies per finding, controller groups deterministically — works |
| v7 | consolidate-to-survivor (one OPEN finding per group) | (running) | tests "7 findings instead of 14" |

Also observed: ingestion scans **auto-launched remediation sessions** for critical/high
findings at scan completion (8 per repo per run → ~42 PRs, since closed). Suspected
switch: presence of `remediation_guidance` in the profile (blanked in v6.1/v7 to test).
For WF this is a control point: the tail must be off for grouped remediation.

Verification quality has been consistently strong: e.g. the stored-XSS note traced
`POST /create` → `todo.content` → `<%- marked(...) %>` with exact file:line and the
vulnerable marked 0.3.5 sanitizer; the meridian JWT finding traced secret → filter →
authorities with line numbers. FP dismissals cite code evidence.

## 7. Working assets (this repo, `swarm-api-poc/`)

- `profile_v3.json` — live profile source of truth (currently v7); `PROFILE-readable.md` — rendered.
- `step1_upload_tranche.py` — xlsx slice + `POST /v1/attachments` (RAVEN's existing handoff shape).
- `step2_create_profile.py` / PATCH — push profile to platform.
- `step3_launch_scans.py` — one ingestion scan per repo + watcher.
- `step4_poll_findings.py` — findings + AVIT accounting.
- `step5_group_findings.py` — deterministic controller-side groupby on GROUP-KEY → prints remediation batches (RAVEN's role).
- `validate_groups.py`, `planner_schema.json` — deterministic group validation (exact-once, overlap checks).
- `mythos_tranche_poc.xlsx` — synthetic 30-AVIT tranche (16 goof + 14 meridian).
- Live profile: `csprof-4ae8ddf3563d443499e681ecc0d74aa5`, org `org_69IXJFLrljx8zSAw`.

## 8. Open questions / next steps

1. Does v7 deliver one open finding per group with exact accounting? (in flight)
2. Does blanking `remediation_guidance` disable the auto-remediation tail? (same run)
3. Set `category` at ingest (family slug) as the stable groupby field; keep the
   fix-file refinement in the note.
4. Plant deliberate false positives in the tranche to measure the FP gate.
5. One real grouped remediation end-to-end (finding → session → PR → reconciliation).
6. Scale posture for WF: scans on the 5,000 CodeScanService pool; remediate calls from
   RAVEN on their 500 pool; Finding Automations only if they accept finding-granular.
7. External claims to keep careful: quota numbers are current implementation, not
   contractual; ingest mode is API-level (not on the public docs page).
