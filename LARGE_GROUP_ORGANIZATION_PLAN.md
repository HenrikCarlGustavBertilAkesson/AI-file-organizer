# Large-Group Organization Improvement Plan

Status: planned; implementation has not started.

This extends [BULK_ORGANIZATION_PLAN.md](BULK_ORGANIZATION_PLAN.md). Existing
category groups, fixed manifests, group approval, and background moves are the
foundation. This plan changes how large proposals are assembled and budgeted;
it does not introduce AI deletion decisions or complete the remaining Trash,
Undo/Restore, and category-editing work in that plan.

## Problem and desired outcome

The organizer currently counts individual file moves against `max_proposals`.
Group tools require explicit member IDs discovered through small pages, and those
members consume the same candidate allowance as files needing AI inspection.
Consequently, selecting 100 candidate files and 10 proposals can produce only
about 10 proposed file moves, spread across small groups.

Separate bounded AI classification from deterministic grouping across the full
eligible workspace index. The AI chooses meaningful existing categories and
policy destinations; application code resolves all matching eligible files into
fixed, reviewable proposals without sending every file to the model.

Illustrative outcome, not measured performance:

- One proposal: move 340 invoices into the saved Finance destination.
- One proposal: move 185 screenshots into the saved Pictures destination.
- A separate review list: 12 files with uncertain category membership.

The actual folder names must come from the saved policy. Do not invent nested
folders or combine unrelated categories merely to increase group size.

## Controls and budget semantics

| Control | Meaning | Suggested initial default |
| --- | --- | --- |
| Files to classify | Maximum distinct files newly classified or explicitly reclassified by AI in this run. | 100 |
| Maximum group proposals | Maximum new user-reviewable move groups created in this run. | 10 |
| Maximum files per group | Maximum members frozen into one proposal. | 1,000 |

Defaults are provisional, to be checked with the focused workload tests below.
Keep existing independent API-attempt, tool-call, context, and response limits.
The classification setting is a ceiling, not a promise to process that many
files if an API budget is exhausted. Permit zero new classifications so users
can organize the already-classified index without reanalysis.

Already-classified members do not consume the classification allowance. A group
with 340 files consumes one proposal slot and 340 member slots in that group,
not 340 proposals. An exceptional single-file move consumes one proposal slot;
legacy tools must not provide a way around the limit. Report group and file
counts separately throughout results, jobs, CLI output, and the dashboard.

## Step 1: Separate budgets and define compatibility

Introduce explicitly named settings such as `classification_limit`,
`max_group_proposals`, and `max_files_per_group`. Centralize validation and
accounting rather than scattering count checks through tools and the agent loop.
Track actual classification work independently from group discovery and member
inspection. Keep read/inspection limits distinct from classification counts.

Version job parameters or otherwise distinguish legacy jobs. Existing saved
manifests and approvals retain their exact membership. Do not reinterpret an old
10-file proposal limit as authorization for 10,000 files, and do not resume old
jobs with silently expanded settings. Require a new run with the new settings
where an old job cannot be mapped without ambiguity.

Completion criterion: a fixture containing hundreds of classified files can
consume one group-proposal slot without consuming new-classification budget;
per-group limits and existing API/tool budgets remain independently enforced.
Legacy jobs have explicit, tested handling.

## Step 2: Assemble full groups in the backend

Add a backend proposal builder using indexed category and policy destination,
scoped to the current workspace and the user's organization request. Select
membership using bounded SQL pages or cursors; do not materialize or serialize
the entire index into a model response.

Apply eligibility filters before pagination and limits: present and classified,
in scope, not protected or excluded, not already at the destination, not blocked
by scan verification failures, and not reserved by another active proposal.
Separate uncertain classifications for review using an explicit, documented
category-confidence rule. Confidence concerns membership, never disposability.

Build groups by normalized category and exact saved destination. Preserve source
filenames. Detect collisions against existing files, all selected members, and
other active proposals. Exclude conflicting members with visible reasons while
allowing unaffected members to form a useful proposal; never overwrite or silently
rename. Unmapped categories require a user-reviewed policy destination.

Persist the explicit member IDs, source snapshots, destinations, policy/scope
context, and group explanation. Reserve the selected source files and destination
paths transactionally with the draft so concurrent or successive runs cannot
create overlapping active proposals. Release reservations according to documented
rejection, completion, invalidation, and interruption handling; uncertain execution
must not simply release files for immediate replay.

If a category exceeds the file cap, split it deterministically into clearly labeled
parts, subject to the group-proposal budget. Report eligible members still outside
the created drafts. Do not pad groups or suppress legitimate small categories.

Completion criterion: 340 eligible files form one fixed proposal with a 1,000-file
cap. With a 100-file cap, they form four parts if the proposal budget permits;
with a two-proposal budget, create two parts and report 140 remaining files.
New index rows never enter an already-created draft.

## Step 3: Give the AI group-level planning tools

Replace mandatory enumeration of every source ID with a tool such as
`propose_category_move(group_id, expected_version)`. The server obtains the
reviewed destination from policy and expands eligible membership under current
limits. The model cannot provide arbitrary SQL, an unrestricted filesystem
selector, or an alternative destination outside policy.

Group summaries should include eligible counts, logical bytes, destination,
uncertain members, conflicts, and files already proposed. Keep member samples
and optional inspection paginated and bounded. Explicitly tell the AI that a
sample is not the full group: it chooses the category operation, while trusted
code applies eligibility rules to every member.

For a focused request, constrain selection to that request's validated scope;
“organize travel documents” must not silently expand into every category. Broader
“organize this workspace” requests may consider all eligible categories. Ambiguous
requests should produce a narrower proposal or seek clarification rather than
silently widening scope.

Prompt the organizer to reuse category groups and favor complete coherent groups
over repeated small subsets. Classify relevant unclassified files only within
the separate budget, refresh summaries, then propose the resulting groups.
Deletion remains entirely user-directed. Preserve bounded member inspection and
single-file exceptions where needed.

Completion criterion: a mocked agent can create a 340-member proposal from a group
summary and a group ID, without receiving 340 paths or issuing hundreds of tool
calls. Repeated calls and successive runs do not duplicate active proposals.

## Step 4: Present large proposals clearly

Replace the current ambiguous controls with the three settings above and short
explanations. Before starting, show available classified files and files needing
classification, so users understand how much organization can happen without
additional analysis.

Display one card per destination group or capped part, including file count,
logical bytes, grouping reason, destination, exclusions, and uncertainty counts.
Keep the complete member list paginated and provide a clear path to inspect the
separate uncertain/conflicting members. Display concise run totals, for example:
“2 group proposals covering 525 files; 12 need review; 140 remain for another run.”

Keep approval bound to the frozen manifest. Viewing another page, completing more
classifications, or adding files cannot expand the operation. Existing individual
child records remain implementation details, not duplicate user-facing proposals.

Completion criterion: users can review and approve one hundreds-file proposal
without loading every member into the browser or seeing hundreds of separate
approval cards. Reported totals agree with persisted membership, including after
reload and across pages.

## Step 5: Adapt background validation and execution to larger groups

Reuse the existing group approval service, strengthening it where larger groups
expose limits. Stream preflight and execution from persisted members in bounded
pages. Keep current source verification, scope/policy checks, conflict detection,
per-file revalidation, durable intents/outcomes, and index updates.

Show separate progress for preparing a proposal, verifying the approved selection,
and moving files. Support cancellation at safe boundaries during each phase.
Do not keep a long database read transaction open while writing progress from
another connection. Define proposal-building cancellation so incomplete manifests
are never exposed as ready for approval.

A bulk move remains non-atomic: some files may finish before a later failure.
Report exact completed, skipped, failed, uncertain, and remaining counts. Preserve
existing prevention of automatic replay after interrupted moves. Handling a
partially executed group must not create fresh proposals for uncertain outcomes
until reconciliation establishes which files actually moved.

Completion criterion: a large approved group executes without unbounded memory,
database lock failures, or browser blocking; cancellation and restart never repeat
completed or uncertain mutations. Tests cover failures around intent, move, receipt,
and index-update boundaries.

## Step 6: Validate effectiveness and document rollout

Create synthetic workspaces containing thousands of small files, several large
categories, a few legitimate small categories, unclassified and uncertain files,
protected projects, scan-failure markers, and filename collisions.

Measure group sizes, proposals per eligible file, files covered per run, remaining
counts, preparation/preflight duration, UI response latency, memory behavior, and
AI request/token usage. Distinguish deterministic backend results, mocked agent
workflow tests, and any explicitly chosen live AI evaluation. Do not infer model
quality from mocked responses.

Required regression cases include changing scope/policy or membership after draft
creation, two requests targeting the same files, new files arriving during approval,
large groups spanning pages, cancelled draft building, partial execution, and old
job settings. Verify that low-confidence or conflicting members cannot silently
enter a high-confidence category proposal.

Completion criterion: demonstrate a hundreds-file single proposal end to end on
disposable fixtures, record measurements and remaining limits, and document the
new settings. These focused tests are required even though the broader scaling
Step 7 remains backlogged; they do not establish whole-Desktop readiness.

## Implementation order and first task

Implement Steps 1–6 in order. Steps 1–2 establish the accounting and backend
contract; Step 3 connects the AI; Steps 4–5 deliver a usable and reliable workflow;
Step 6 verifies the improvement before recommending broader use.

Start with **Step 1: separate budgets and define compatibility**. Add validated
settings and distinct counters, specify legacy-job handling, and test that group
size no longer consumes the proposal count or classification allowance. Do not
merely increase the existing `max_proposals` or context limit: that would preserve
the underlying per-file bottleneck.
