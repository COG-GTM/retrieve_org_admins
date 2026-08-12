# Raven Mythos Ingest v8 — strict-format consolidate-to-survivor, category at ingest

> Imports the open Mythos AVIT records for the scanned repository from an attached Excel workbook, stamps each imported finding with a coarse remediation-family category at ingest time, verifies each against the source code (dismissing false positives with evidence), then consolidates each remediation group to a single surviving open finding whose note starts with strict machine-readable header lines (GROUP-KEY, MEMBER-AVITS, EXPECTED-FILES). Final state: one open finding per remediation group (hard target 5-7 groups per ~15 AVITs), each a self-contained one-session/one-PR work order. AVIT identifiers preserved for reconciliation.

## Ingestion Source Guidance

Findings come from a Mythos AVIT export attached to this scan as an Excel workbook (.xlsx) — the same table of AVIT records RAVEN passes to Devin today. There is no API to query and no database to connect to.

Locating the file:
1. Use the workbook attached to this scan run.
2. If no workbook is attached, stop and report that the AVIT export is missing. Never invent, infer, or discover findings — this profile only imports what Mythos reported.

Parsing:
- Read the first worksheet unless another sheet is clearly the AVIT table (e.g. a sheet named 'avit_table' or 'Findings'). Row 1 is the header row.
- Address columns BY HEADER NAME, not position — column order varies between exports and teams add columns. Match case-insensitively and treat spaces, hyphens and underscores as equivalent ('Finding ID' == 'finding_id').
- Expected columns, with common variants in parentheses: avid (avit, avit_id), finding_id (id, issue_id), repo, app_id, business_unit, group_id (if RAVEN already assigned one), cwe, class (category, vuln_type), severity plus any per-source severity columns, confidence, title, file_path (file, path, location), start_line (line), end_line, code_snippet (snippet, evidence), description, remediation (recommendation, fix), detected_at, model (scanner_version), status, mythos_url (link, url).
- Minimum needed to import a row: avid (or finding_id), file_path, description. Skip rows lacking them and report the count of skipped malformed rows.
- Skip rows whose repo column names a different repository than the one being scanned, and report how many.
- Import only open AVITs: skip status values like closed, fixed, resolved, accepted, risk-accepted, wont-fix, ignored, and report how many were skipped for that reason.
- If the same avid appears more than once (appended exports, multiple sheets), import it once.

Carry onto each imported finding so it stays traceable back to Mythos and to RAVEN:
- avid as the source identifier — this is the key RAVEN reconciles on and it must never be lost, altered, or merged away. Also carry finding_id, mythos_url, and any incoming group_id.
- The original title, description and remediation text VERBATIM. Do not paraphrase Mythos's claim before classifying it; classification needs the original wording to judge it.
- cwe, class, model, detected_at, confidence, app_id, business_unit as metadata. Keep confidence visible on the finding — useful signal for reviewers, but never grounds for a verdict on its own.
- Severity mapped straight through: Critical -> critical, High -> high, Medium -> medium, Low/Informational/Info -> low. Preserve the original Mythos severity string alongside the mapped value so any reprioritization is auditable.
- Location as repo-relative file_path plus start_line-end_line. If the line numbers no longer match the current code (the export may predate recent commits), re-anchor using code_snippet and note that the line was adjusted. Never drop a finding solely because its line number is stale.

CATEGORY STAMPING (mandatory): when importing each finding, set its category/vuln_slug to the remediation-family slug it belongs to, judged from the AVIT's CWE ID and description. Allowed family slugs (use ONLY these; when in doubt pick the closest — prefer merging over precision): injection (SQL/NoSQL/command/template/prototype-pollution — anything where untrusted input reaches an interpreter or object graph), xss-encoding (ALL output-encoding issues: reflected XSS, stored XSS, open redirect via unvalidated destination), hardcoded-secrets (ALL hardcoded credentials, secrets, tokens, default/seeded accounts), missing-authz (ALL missing/broken authentication or authorization: unauthenticated routes, missing role checks, mass assignment of privilege, trusting client-supplied identity/claims), session-config (session/cookie/JWT storage and configuration weaknesses), path-traversal (file path/zip handling), dos (ReDoS, resource exhaustion), config-hardening (framework/server misconfiguration not covered above). The customer performs a deterministic GROUP BY on this field, so every imported finding MUST have exactly one of these slugs.

## Triage Guidance

Triage runs in two passes using per-finding dispositions only (never create new findings).

PASS 1 — verify. Verify each imported finding against the actual code. Dismiss false positives (with file:line evidence and a named reason). Never dismiss solely because confidence is low.

PASS 2 — consolidate to one survivor per group. Group = the finding's family category (stamped at ingest), refined by one hard rule: findings whose fixes change the same file MUST be in the same group even across families. Allowed family slugs (use ONLY these; when in doubt pick the closest — prefer merging over precision): injection (SQL/NoSQL/command/template/prototype-pollution — anything where untrusted input reaches an interpreter or object graph), xss-encoding (ALL output-encoding issues: reflected XSS, stored XSS, open redirect via unvalidated destination), hardcoded-secrets (ALL hardcoded credentials, secrets, tokens, default/seeded accounts), missing-authz (ALL missing/broken authentication or authorization: unauthenticated routes, missing role checks, mass assignment of privilege, trusting client-supplied identity/claims), session-config (session/cookie/JWT storage and configuration weaknesses), path-traversal (file path/zip handling), dos (ReDoS, resource exhaustion), config-hardening (framework/server misconfiguration not covered above). HARD TARGET: 5-7 groups total for the repository (fewer is fine; more than 7 requires explicit justification per extra group — prefer merging over precision). For each group elect exactly ONE survivor (highest severity; ties: most complete). Dismiss every non-survivor with reason 'CONSOLIDATED into <survivor finding id> (group <key>) — true positive, tracked on the survivor'.

SURVIVOR NOTE FORMAT (mandatory, machine-parsed): the survivor's note MUST BEGIN with exactly these header lines, one field per line, no markdown, no prose on these lines, copied in this exact format:
GROUP-KEY: <family-slug>|<primary-fix-file>
MEMBER-AVITS: <comma-separated AVIDs of ALL members incl. the survivor>
EXPECTED-FILES: <comma-separated repo-relative files the group's one PR will change>
GROUP-RATIONALE: <one sentence>
After a blank line, append per-member blocks: AVID, title, verified evidence (file:line), expected fix change.

SELF-CHECK (mandatory, before finishing): re-read every OPEN finding and verify (1) its note starts with the four header lines in exactly the format above, (2) every imported AVIT appears in exactly one survivor's MEMBER-AVITS or exactly one dismissal, (3) no AVIT appears twice, (4) open finding count is within the 5-7 target or justified. Fix any violation before ending.

## Post Ingestion Guidance

This scan has two jobs after import: (A) false-positive classification against the real code, and (B) Consolidation — follow the triage guidance exactly: verify, then one survivor per group (family category + same-fix-file merge rule), strict survivor note header format, mandatory self-check. The customer remediates one session per surviving finding.

## Report Guidance

Executive summary first: AVITs imported (and rows skipped, by reason), the split into confirmed / dismissed as false positive / needs human review, the false-positive rate, and the number of remediation groups produced with the average group size.

Then:
- Remediation groups — table: Group | Member AVITs | Shared root cause | Expected files to change | Severity | Confidence | Conflicts-with.
- Dismissed as false positive — table: AVIT | Location | Dismissal reason (unreachable / mitigated / non-production code / hallucinated location / misread semantics) | file:line evidence. The evidence citation is mandatory for every row.
- Reprioritized — the AVIT, Mythos's severity, the new severity, and the reachability reasoning.
- Needs human review — the AVIT, what is unresolved, and the exact question a reviewer must answer.
- Grouping comparison — where the groups differ from any incoming group_id, and why.
- Signal quality for tuning Mythos — false-positive rate by CWE/class and by confidence band, and the recurring patterns behind the false positives (e.g. 'does not resolve route-level auth middleware', 'flags test fixtures as hardcoded secrets').
- Accounting line: imported N = grouped X + dismissed Y + needs-review Z.

---
`scan_type: security` · `mode: ingest` · profile `csprof-4ae8ddf3563d443499e681ecc0d74aa5`
