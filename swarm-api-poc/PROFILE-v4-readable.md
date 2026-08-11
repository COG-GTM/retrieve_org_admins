# Scan Profile — Raven Mythos Ingest v4

Live profile: `csprof-4ae8ddf3563d443499e681ecc0d74aa5` (Security > Profiles). 
Source of truth: `profile_v3.json`; pushed to the platform via the API.

## Name

`Raven Mythos Ingest v4 — AVIT workbook, FP classification + remediation grouping`

## Description

Imports the open Mythos AVIT records for the scanned repository from an Excel workbook attached to the scan (the same avit_table RAVEN passes to Devin today), classifies each against the source code to separate true positives from false positives, then consolidates the survivors into remediation groups: one consolidated finding per group of AVITs that share a root cause and can be fixed as one coherent PR. Each finding carries its member AVITs so RAVEN can reconcile AVIT -> group -> session -> PR.

## Scan type

`security`

## Mode

`ingest`

## Ingestion source guidance — where findings come from & what to preserve

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

## Triage guidance — the grouping policy (v4: moved here)

Grouping is the primary output of triage in this profile. Deduplicate and consolidate the confirmed AVITs into REMEDIATION GROUPS, then emit ONE finding per group — not one finding per AVIT. A group is a set of AVITs that share a root cause and can be fixed as one coherent, reviewable pull request.

Rules:
- Merge AVITs that share a root cause: the same sink reported at several lines, one flaw filed under two CWEs, a flaw split across a handler and the helper it calls, several routes missing the same sanitizer or guard, or several AVITs fixed by one dependency upgrade or one configuration change. The merged finding must list every member avid.
- Scanner-reported file_path and cwe are input signals, not the policy. AVITs in the same file do not always share a fix; AVITs in different files often do.
- Do NOT merge AVITs that merely share a category or severity. When in doubt, keep them separate and note the possible relationship.
- An AVIT with a genuinely distinct root cause stays as its own single-AVIT group. A 1:1 outcome is correct when the findings really are unrelated — say so explicitly rather than forcing merges.
- Cap a group at 8 AVITs; split larger ones along sub-causes and explain the split.
- Populate related_finding_ids between groups whose expected files to change overlap, and note the conflict in both, so the controller can serialize their remediation instead of merging unrelated fixes.

Every emitted finding must state: member AVITs, shared root cause, expected files to change, grouping rationale and confidence, recommended tests. State the accounting explicitly: imported = grouped + dismissed + needs-human-review, and report the number of groups and the average group size.

Severity of a group is the highest severity among its members. Treat unauthenticated RCE, auth bypass, and injection reaching a sensitive sink as critical/high; hardcoded live credentials as high; defense-in-depth recommendations as low.

## Post-ingestion guidance — FP classification + group contents

This scan has two jobs after import: (A) false-positive classification against the real code, and (B) consolidation of the survivors into remediation groups. The scan's output findings are REMEDIATION GROUPS, not individual AVITs. The scan does not write fixes.

(A) FP classification — every imported AVIT is an unverified claim produced by an LLM scanner:
1. Open the cited file and read the real code around the cited lines. Never reason from the code_snippet column alone; it may be truncated, paraphrased, or hallucinated.
2. Establish reachability: identify a concrete untrusted entry point (HTTP handler, route registration, CLI argument, message consumer, uploaded file) and trace the data flow from it to the sink the AVIT describes. It is a true positive only if attacker-controlled data actually reaches that sink.
3. Look cross-file before deciding. LLM scanners routinely miss route-level auth/authorization middleware, validation or sanitization in an upstream wrapper, framework-wide controls (auto-escaping, parameterized ORM binding, CSRF middleware), or the fact that the function is never called at all.

Dismiss as a false positive — always citing specific file:line evidence and naming the reason — when: the sink is unreachable from untrusted input (constant/hardcoded value, trusted internal callers only, dead code); an effective control exists on the real path; the code is not part of the deployed application (tests, fixtures, examples, exploit scripts, docs, local dev/build tooling, generated or vendored code — credentials in test fixtures are fixtures, but flag any that look real); the cited location does not contain the described code and the pattern is nowhere in that module (hallucinated location); or the description misreads the code's semantics — state precisely what the code actually does.

Do NOT dismiss because confidence is low (low confidence means investigate harder), because the category is often noisy, or because the fix looks difficult. For confirmed AVITs, correct severity where the code justifies it and give the reachability reasoning. A live (non-fixture) hardcoded credential stays high or above and must note that rotation is required in addition to the code fix.

(B) Grouping — the core of this profile:
- Group confirmed AVITs that share a ROOT CAUSE and can be fixed as ONE coherent, reviewable change. Scanner-reported file_path and cwe are input signals, NOT the policy: AVITs reported in the same file do not always share a fix, and AVITs in different files often do (one missing sanitizer used by many routes, one vulnerable helper, one config flag, one dependency bump).
- Emit ONE consolidated finding per group. Each consolidated finding MUST state, in its description, in this order:
  1. AVITs: the complete list of member avid values (with finding_ids). Every imported AVIT must end up in exactly one of: a group, the dismissed list (with file:line evidence), or needs-human-review.
  2. Shared root cause, in one or two sentences.
  3. Expected files to change: the files the FIX will touch. This is a prediction about the change set and may differ from where Mythos reported the AVITs.
  4. Grouping rationale and confidence (high / medium / low).
  5. Recommended tests for the fix.
- Keep groups reviewable: if a group would exceed 8 AVITs, split it along sub-causes and say why.
- Duplicates: AVITs describing the same flaw (same sink at several lines, one flaw filed under two CWEs, a flaw split across a handler and the helper it calls) merge into one group; list every member avid so nothing is lost.
- If two groups' expected files to change overlap, note the conflict on both findings so the controller can serialize their remediation instead of merging unrelated fixes.
- If an incoming group_id was supplied, treat it as a hint only. Where your grouping differs, say so and explain why — that comparison is exactly the signal RAVEN wants.

Accounting is mandatory and must be stated explicitly: imported = grouped + dismissed + needs-human-review. An AVIT that cannot be confidently classified or grouped becomes its own single-AVIT finding marked needs human review, with the exact open question. Never resolve ambiguity by guessing in either direction.

## Report guidance — what the human-readable report contains

Executive summary first: AVITs imported (and rows skipped, by reason), the split into confirmed / dismissed as false positive / needs human review, the false-positive rate, and the number of remediation groups produced with the average group size.

Then:
- Remediation groups — table: Group | Member AVITs | Shared root cause | Expected files to change | Severity | Confidence | Conflicts-with.
- Dismissed as false positive — table: AVIT | Location | Dismissal reason (unreachable / mitigated / non-production code / hallucinated location / misread semantics) | file:line evidence. The evidence citation is mandatory for every row.
- Reprioritized — the AVIT, Mythos's severity, the new severity, and the reachability reasoning.
- Needs human review — the AVIT, what is unresolved, and the exact question a reviewer must answer.
- Grouping comparison — where the groups differ from any incoming group_id, and why.
- Signal quality for tuning Mythos — false-positive rate by CWE/class and by confidence band, and the recurring patterns behind the false positives (e.g. 'does not resolve route-level auth middleware', 'flags test fixtures as hardcoded secrets').
- Accounting line: imported N = grouped X + dismissed Y + needs-review Z.

## Remediation guidance — rules for the later fix sessions (not used by the scan)

A remediation session receives ONE consolidated finding = one remediation group. Fix every member AVIT of that group in a single coherent PR, and nothing else.

Fix the actual root cause identified during classification, not merely the lines Mythos cited. Mythos's remediation text is a suggestion written without full repository context — follow it only where it matches what the code needs.

Keep the change minimal and local. Follow the conventions of the surrounding code (error handling, validation helpers, configuration access) and prefer an existing utility in the repo over introducing a new dependency. Do not modify tests, fixtures, or example/exploit scripts to make a finding go away. Avoid major dependency upgrades unless the vulnerability cannot be fixed safely without one.

Add or update tests per the group's recommended tests: at least one regression test that fails before the fix and passes after. Run the repository's lint, build, and test commands and make them pass before opening the PR.

For hardcoded credentials, move the value to configuration or a secret manager, remove any logging of it, and state in the PR that the exposed credential still needs rotation — the code change alone does not remediate it.

The PR description must list every member AVIT (avid, finding_id, mythos_url), the shared root cause, the files changed, and any behaviour change a reviewer should watch for. If, mid-fix, an AVIT turns out not to share the group's root cause, fix the group's true members, leave that AVIT unfixed, and say so explicitly in the PR so the controller can regroup it rather than recording a fix that did not happen.
