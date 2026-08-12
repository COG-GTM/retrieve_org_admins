# RAVEN × Security Swarm — Converged Solution

Companion to `RAVEN-CONTEXT.md` (background, motivation, experiment log). This document
is the solution itself: the architecture we converged on, why each piece landed where it
did, and the exact contracts between components.

---

## 1. The solution in one paragraph

RAVEN keeps its role as the fleet control plane (tranche intake, repo mapping, launch,
reconciliation), but the middle of its pipeline — parse, verify, false-positive triage,
grouping — moves out of one overloaded Devin session and into a **Security Swarm
ingestion scan per repo**. The scan imports the Mythos AVITs from the same attachment
RAVEN already produces, verifies each one against the real code in parallel workers,
dismisses false positives and duplicates with written evidence, and **stamps every
finding with a remediation-family `category`** from a fixed enum defined in the profile.
RAVEN then does a plain `GROUP BY category` on the findings API response (~5–7 groups
per repo), triggers exactly **one remediation per group** via the group representative's
remediate endpoint, and reconciles by reading the `session_id`/`pr_url` the platform
stamps back onto the finding.

```
Mythos AVIT tranche (xlsx)
  RAVEN │ POST /v1/attachments                       (unchanged from today)
        │ POST .../code-scans/ingestion  × N repos   (replaces create-session)
  Swarm │ import → stamp category (family enum) → verify each AVIT vs code (FP gate)
  RAVEN │ GET .../findings?scan_id=… → GROUP BY category → validate accounting
        │ POST .../code-scans/{scan_id}/findings/{rep_id}/remediate × one per group
  Swarm │ one session per group → one PR per group; session_id + pr_url
        │   stamped back on the finding
  RAVEN │ reconcile PRs → AVIDs via MEMBER-AVITS / external_id
```

## 2. Security Swarm concepts — the vocabulary this solution is built on

Everything below was established during the PoC (docs + verified API behavior + source
tracing where noted). Getting these object boundaries right was half the work.

### The object model

- **Profile** — a reusable *scan policy*: scope + per-stage guidance text
  (`ingestion_source_guidance`, `triage_guidance`, `post_ingestion_guidance`,
  `report_guidance`, `remediation_guidance`, plus discover-mode stages like
  `threat_model_guidance` / `investigation_guidance` / `validation_guidance`). Think
  "playbook whose output is findings, not a PR". One profile serves the whole fleet;
  it is parameterized at launch by `repo_name` + `attachment_urls`. Policy-as-code:
  WF's grouping rules live here, versioned and reviewable. Updated via PATCH.
- **Scan** — one *execution* of a profile against one repo. Not a session: it's an
  orchestrator Devin plus parallel worker Devins ("Agentic MapReduce"). Polled via the
  scans list endpoint (there is no per-scan GET).
- **Mode** — a profile field with exactly two values, choosing where leads come from:
  - `discover`: Swarm finds vulnerabilities itself by analyzing the repo.
  - `ingest`: Swarm starts from *supplied external findings* (our case — Mythos).
  Independent of `scan_type` (security, code-quality, …; only `security` is enabled in
  this org).
- **Threat model** — a per-RUN, repo-specific analysis artifact (entry points, trust
  boundaries, attacker personas) generated in discover mode only. It is not a profile
  and doesn't exist in ingest mode — the imported findings ARE the leads ("Mythos is
  the threat model").
- **Finding** — the durable, queryable deliverable of a scan: title, severity, status
  (Open / Dismissed / Reviewed), confidence, `category`/`vuln_slug`, `external_id`,
  evidence-bearing `note`, and — after remediation — `session_id` + `pr_url`. A finding
  is NOT a PR; it's the record that a remediation can later be launched from.
- **Remediation** — an *action on a finding* (UI "Assign to Devin", the remediate API,
  or an automation). It launches an **ordinary top-level Devin session** — the same
  kind RAVEN creates today — just pre-seeded with the finding's verified evidence, and
  with the resulting session/PR stamped back on the finding. Swarm's only "special"
  execution is the scan itself.

### Discover vs ingest — phase pipelines (source-verified)

| | `discover` | `ingest` |
|---|---|---|
| Leads come from | Threat-model child: builds threat model, produces signal batches | Ingest child: imports customer findings from the attachments, creating finding records up front |
| Interactive pause | User approves threat model before spend | User reviews import summary + duplication signal |
| Middle (map) | Batch investigation: one child per batch in parallel (up to ~100 concurrent), open-ended hunting | Triage: finding IDs batched, one child per batch in parallel, targeted verification of specific claims |
| Consolidation | Aggregation child: dedupe, ownership enrichment, attack chains (`related_finding_ids`), P0–P3 triage | **None** — no aggregation phase exists; only per-finding dispositions |
| Tail (shared) | Optional runtime validation (exploit in sandbox), report | Same shared validation/report phases |

Practical consequences we designed around:
- Ingest-mode findings appear in the UI at import time, already enriched, then triage
  children mutate them — there is no "reduce" step that sees everything at once.
- Triage dispositions are the complete vocabulary: **dismiss** (`false_positive`,
  `mitigated`, `duplicate`, `accepted_risk`, `not_actionable`), **adjust severity**,
  or **keep open** — plus rewriting the note. Nothing merges findings into new
  objects, hence the consolidate-to-survivor pattern (§4).
- `category`/`vuln_slug`/`external_id` are settable **only at ingest**; the finding
  update path cannot change category later. Notes are the triage-time channel.
- Ingest mode is purpose-built for this use case, per its orchestrator prompt: "the
  customer already has findings from their own tooling, and this scan imports them so
  they can be managed here."

### Execution identities and quota (source-verified)

Session quota is checked per (user, org) for every session including children:
- Scan orchestrators and ALL scan workers → `CodeScanService` identity: **5,000**
  concurrent/org. Scan fan-out never touches RAVEN's pool.
- Direct API calls with WF's key (`bot_apk`) → their **500** pool (remediate included).
- Automation-triggered sessions → automation service: **1,000**.
(Current implementation values, not contractual.)

## 3. Division of labor (the core design decision)

| Concern | Owner | Why |
|---|---|---|
| Tranche intake, repo mapping, launch, caps, retries | RAVEN | Fleet-level control stays in WF's code |
| AVIT parsing / import | Swarm ingest child | Purpose-built: creates durable finding records from the attachment |
| Verification vs real code (FP gate) | Swarm triage children | Needs code access + AI judgment; runs parallel on the scan-service quota pool |
| Duplicate / FP dismissal | Swarm triage children | Per-finding disposition is the mechanism ingest mode actually has |
| Group **classification** (family, primary fix file) | Swarm (AI, per finding) | Semantic judgment — which family, where the fix really lands |
| Group **synthesis** (one record per group) | Swarm (consolidate-to-survivor) | Expressible as dispositions: elect survivor, dismiss members into it |
| Accounting **validation** (exact-once, overlap) | RAVEN (deterministic code) | Must be auditable; no LLM in the control path |
| Remediation trigger | RAVEN → finding remediate endpoint | One call per group; session pre-seeded with the survivor's verified evidence |
| Fix + PR | Ordinary Devin session | Unchanged from what WF already trusts |
| Reconciliation | RAVEN | Field reads (`session_id`, `pr_url`, note header), not PR-text parsing |

Guiding principle: **AI does per-finding semantic judgment; deterministic code does
grouping arithmetic and accounting.** Everything that must be auditable (every AVIT
exactly once, no overlapping groups, group count in range) is a string operation RAVEN
performs on structured fields — never an LLM output taken on faith.

## 4. The grouping mechanism (what we converged on and why)

WF's review policy: ~5–7 groups per ~20 AVITs, grouped by CWE family, with a hard
same-fix-file merge rule, one PR per group.

Facts that shaped the design (source-verified):
- Ingest mode has **no aggregation phase** — nothing natively merges findings into new
  group objects. Its triage is parallel per-finding dispositions: dismiss / adjust /
  keep.
- Findings DO have an ingest-time **`category`/`vuln_slug`** field, exposed by the
  findings API, but it cannot be changed later in triage.
- Notes CAN be rewritten in triage, and dismissals carry reasons.

So the mechanism is layered:

1. **Ingest-time `category`** = coarse remediation-family slug (from a fixed enum in
   the profile: `injection`, `xss-encoding`, `hardcoded-secrets`, `missing-authz`,
   `session-config`, `path-traversal`, `dos`, `config-hardening`). Robust, queryable,
   groupby-able without note parsing. **This is the primary grouping mechanism** —
   empirically 100% populated and it alone produced the target shape (meridian 4
   groups, goof 7 groups for 30 AVITs).
2. **Triage pass 1 — verify**: each AVIT checked against the code; FPs dismissed with
   file:line evidence (this gate caught real FPs unprompted, e.g. an SSTI neutralized
   by template auto-escaping).
3. **(Attempted, then rejected) in-scan consolidate-to-survivor**: v7/v8 tried to make
   the scan itself end with one OPEN finding per group (elect a survivor, dismiss
   members as CONSOLIDATED, strict machine header in the survivor's note). It worked
   partially (v7 meridian 14→8 open) but is **structurally unreliable**: triage fans
   out as parallel batch children, each seeing only its own findings, with **no global
   reduce step** — so cross-batch survivor election only succeeds when a group happens
   to land in one batch. Verdict: do not depend on it. The finding list stays
   per-AVIT; the *group* is the work-order unit, tracked controller-side.
4. **RAVEN groupby + validation** (deterministic): `GROUP BY category`, assert every
   AVIT in exactly one group or one dismissal, no duplicates, group count in range,
   verified fix files non-overlapping across groups (merge groups that overlap) — this
   is `step5_group_findings.py` + `validate_groups.py` / `planner_schema.json`.

Result shape: the UI shows N verified per-AVIT findings, but the **work-order count is
the group count** — e.g. 30 AVITs → 11 groups → 11 sessions → 11 PRs.

### Evolution that got here (details in RAVEN-CONTEXT.md §6)
- v3–v4: asked the scan to "group" → either 1:1 output or double-counted AVITs. Lesson:
  no native merge mechanism; unconstrained AI grouping breaks accounting.
- v5: WF policy verbatim → excellent verification/FP work, zero consolidation. Lesson:
  per-finding dispositions can't merge unless told HOW (dismiss-into-survivor).
- v6: GROUP-KEY tag + controller groupby → worked (meridian 7 groups, exact-once by
  construction) but leaves N open findings and needs the controller to group.
- v7: consolidate-to-survivor → merging partially works (meridian 14→8 open), but
  free-prose notes broke machine parsing and fine-grained families under-merged goof.
- v8: strict header template + self-check + coarse family enum + ingest-time category
  stamping → consolidation regressed (parallel triage batches have no global reduce),
  but **category stamping worked perfectly** and a plain groupby on it hit the target
  shape. Final mechanism: category groupby controller-side; in-scan consolidation
  treated as best-effort only.

## 5. Remediation and the control point

- **Trigger**: RAVEN calls
  `POST /v3/organizations/{org}/code-scans/{scan_id}/findings/{finding_id}/remediate`
  once per group, on the group's representative (highest-severity) finding. Verified
  201 + `{finding_id, session_id}`; returns 409 if the finding already has a
  remediation session. The endpoint takes **no prompt body** — the session is seeded
  from the finding's record. To widen the session's scope to the whole group, send a
  follow-up message via `POST /v1/session/{session_id}/message` listing the group's
  other members (verified working in the PoC).
- **Quota**: scan fan-out (orchestrator + all workers) runs on the CodeScanService
  identity (5,000 concurrent/org) — it does NOT consume RAVEN's 500-session `bot_apk`
  pool. Remediate calls made with WF's key run on their 500 pool; automation-triggered
  sessions would use the automation-service pool (1,000).
- **Finding Automations** trigger per finding. Post-consolidation, "per finding" ==
  "per group", so a capped automation becomes viable — but the general automations
  engine has no code-scan-finding event today, so the near-term trigger is RAVEN's
  thin poll-and-remediate loop.
- **⚠ Open control point — the auto-remediation tail**: ingestion scans have
  auto-launched remediation sessions for critical/high open findings at scan
  completion, in every run observed — including with `remediation_guidance` blanked
  (that hypothesis is disproven). Post-consolidation the tail fires per GROUP, which
  matches the desired end state, but the switch that gates it is not yet identified;
  it needs a product-level answer before WF can rely on either "always off" or
  "always on, capped". Until then, treat the tail as ON for crit/high findings.

## 6. Contracts (exact API surface)

```
POST /v1/attachments                                   # multipart xlsx → URL
POST /v3/organizations/{org}/code-scans/ingestion      # {repo_name, profile_id, attachment_urls}
GET  /v3/organizations/{org}/code-scans/scans?limit=N  # poll status (list; no per-scan GET)
GET  /v3/organizations/{org}/code-scans/findings?scan_id=…&limit=N
POST /v3/organizations/{org}/code-scans/{scan_id}/findings/{finding_id}/remediate   # 201 {finding_id, session_id}; 409 if already remediated; no prompt body
POST /v1/session/{session_id}/message                  # widen session scope to the full group
PATCH /v3beta1/organizations/{org}/code-scans/profiles/{profile_id}   # profile updates (PUT=405)
```

Finding fields RAVEN consumes: `status`, `severity`, `category` (family groupby),
`external_id`/AVIDs, `note` (machine header), `session_id`, `pr_url`.

One reusable **profile** serves the whole fleet (`csprof-4ae8ddf3563d443499e681ecc0d74aa5`
in the PoC); per-repo variation comes only from `repo_name` + `attachment_urls` at
launch. Profile = policy-as-code: WF's grouping rules live in versioned guidance text,
reviewable like any config.

## 7. What each PoC file proves

| File | Role in the solution |
|---|---|
| `step1_upload_tranche.py` | RAVEN's existing attachment handoff, unchanged |
| `profile_v3.json` (v8) | The policy: verification, FP gate, ingest-time family-enum category stamping |
| `step3_launch_scans.py` | One ingestion scan per repo + watch loop |
| `step4_poll_findings.py` | Findings pull + AVIT accounting |
| `step5_group_findings.py` | RAVEN's deterministic side: GROUP BY category (GROUP-KEY fallback), print the per-group remediate calls |
| `validate_groups.py`, `planner_schema.json` | The audit gate: exact-once, overlap, size checks |

## 8. Why this beats the status quo (the pitch, tied to their pain)

1. **Context**: the fix-writing session no longer carries parsing + FP triage +
   grouping — it gets one verified, pre-scoped group. (Their #1 quality complaint.)
2. **Review shape**: 5–7 coherent PRs per repo tranche instead of one mega-PR or 20
   scattershot ones; the same-file rule kills the merge-conflict class of pain.
3. **FP gate before engineering spend**: verified dismissals with evidence, in a
   durable record an auditor can read — not buried in a session transcript.
4. **Traceability**: every AVID lives in exactly one survivor's header or one
   dismissal; PR → session → finding → AVIDs is field reads, not text mining.
5. **Quota**: the heavy fan-out moved to the 5,000-slot scan pool; RAVEN's 500
   sessions are spent only on actual fixes.
6. **Auditability**: the AI's judgment is captured per finding; the grouping
   arithmetic that must be exact runs as deterministic, reviewable controller code.

## 9. Remaining items before production posture

1. Done — v8 settled the mechanism question: category groupby is the reliable path;
   in-scan consolidation is not (no global reduce across triage batches).
2. Get the product-level answer on the auto-remediation tail switch.
3. Run one grouped remediation end-to-end (survivor → session → PR → reconciliation)
   as the final demo leg.
4. Plant deliberate FPs in a tranche to measure the FP gate's precision/recall.
5. Scale test: one tranche across many repos in parallel (the PoC used 2).
