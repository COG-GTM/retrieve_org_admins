# Raven Mythos Ingest v7 — consolidate-to-survivor: one open finding per remediation group

> Imports the open Mythos AVIT records for the scanned repository from an attached Excel workbook, verifies each against the source code (dismissing false positives and duplicates with evidence), assigns each confirmed finding a GROUP-KEY per the customer's review policy (CWE family + primary fix file), then CONSOLIDATES each group to a single surviving open finding that carries every member AVIT. Final state: one open finding per remediation group (~5-7 per ~15 AVITs), ready for one remediation session / one PR each. AVIT identifiers preserved for reconciliation.

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

## Triage Guidance

Triage runs in two passes using per-finding dispositions only (never create new findings).

PASS 1 — verify and key. Verify each imported finding against the actual code. Dismiss false positives (with file:line evidence and a named reason) and exact duplicates (keep the most complete as survivor). Never dismiss solely because confidence is low. For every finding that remains, choose a GROUP-KEY = <family>|<primary-fix-file>: <family> is a stable remediation-family slug (e.g. injection, xss, hardcoded-credentials, authz, session-config, dependency-upgrade, config-hardening) shared by findings whose fixes follow the same pattern; <primary-fix-file> is the repo-relative file the FIX will primarily change (from your verification, not the scanner-cited location). Customer policy: findings sharing a family SHOULD share a key; findings whose fixes change the same file MUST share a key. Target roughly 5-7 distinct keys per ~15-20 confirmed findings — prefer reusing an existing key over inventing a new one; a single-member key needs explicit justification.

PASS 2 — consolidate to one survivor per key. For each GROUP-KEY, elect exactly ONE survivor: the member with the highest severity (ties: the most complete/central finding). Update the survivor's note to be the group record, containing:
GROUP-KEY: <key>
MEMBER-AVITS: <comma-separated list of ALL member AVIDs, including the survivor's>
then, for each member, a short block: its AVID, title, verified evidence (file:line), and expected fix change. End with EXPECTED-FILES: <all files the group's one PR will change> and GROUP-RATIONALE: <one sentence>.
Dismiss every non-survivor member with reason 'CONSOLIDATED into <survivor finding id> (group <key>)' — this is a grouping disposition, not a judgment that the issue is invalid; say so in the note. Adjust the survivor's severity to the group's highest.

Accounting is mandatory and exact: every imported AVIT must appear in exactly one of (a) exactly one survivor's MEMBER-AVITS list, (b) a false-positive dismissal, (c) a duplicate dismissal, or (d) a needs-human-review note. No AVIT may appear in two survivors. Final state: open findings == number of groups, each a self-contained remediation work order (one session, one PR).

## Post Ingestion Guidance

This scan has two jobs after import: (A) false-positive classification against the real code, and (B) consolidation of the survivors into remediation groups. The scan's output findings are REMEDIATION GROUPS, not individual AVITs. The scan does not write fixes.

(A) FP classification — every imported AVIT is an unverified claim produced by an LLM scanner:
1. Open the cited file and read the real code around the cited lines. Never reason from the code_snippet column alone; it may be truncated, paraphrased, or hallucinated.
2. Establish reachability: identify a concrete untrusted entry point (HTTP handler, route registration, CLI argument, message consumer, uploaded file) and trace the data flow from it to the sink the AVIT describes. It is a true positive only if attacker-controlled data actually reaches that sink.
3. Look cross-file before deciding. LLM scanners routinely miss route-level auth/authorization middleware, validation or sanitization in an upstream wrapper, framework-wide controls (auto-escaping, parameterized ORM binding, CSRF middleware), or the fact that the function is never called at all.

Dismiss as a false positive — always citing specific file:line evidence and naming the reason — when: the sink is unreachable from untrusted input (constant/hardcoded value, trusted internal callers only, dead code); an effective control exists on the real path; the code is not part of the deployed application (tests, fixtures, examples, exploit scripts, docs, local dev/build tooling, generated or vendored code — credentials in test fixtures are fixtures, but flag any that look real); the cited location does not contain the described code and the pattern is nowhere in that module (hallucinated location); or the description misreads the code's semantics — state precisely what the code actually does.

Do NOT dismiss because confidence is low (low confidence means investigate harder), because the category is often noisy, or because the fix looks difficult. For confirmed AVITs, correct severity where the code justifies it and give the reachability reasoning. A live (non-fixture) hardcoded credential stays high or above and must note that rotation is required in addition to the code fix.

(B) Consolidation — follow the triage guidance's two-pass policy exactly: verify + key, then one survivor per GROUP-KEY with all member AVITs folded into the survivor's note and non-survivors dismissed as CONSOLIDATED. The customer remediates one session per surviving finding.

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
