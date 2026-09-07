# Scaling and Large-Folder Usability Plan

## Milestone

Enable users to browse a large folder, choose what belongs in scope, and process
it in manageable batches with visible progress.

The current application has been tested primarily with small folders. It has
not yet been benchmarked against the user's Desktop workload of more than
6,000 files. A larger folder requires improvements to scope selection,
responsiveness, AI workload limits, and organization consistency.

## Implementation Roadmap

| Step | Implementation | Completion criterion |
| --- | --- | --- |
| 1. Folder inventory and exclusions | Preview file counts and sizes by subfolder. Let users select folders and exclude generated directories or bundles. Persist the selection. | Users can see and control the scope before hashing files or calling AI. |
| 2. Background jobs and progress | Move long operations out of HTTP requests. Add progress, cancellation between files, and resumable processing. | The dashboard remains responsive while scanning or classifying thousands of files. |
| 3. Paginated library and queries | Fetch only the columns and rows needed. Add pagination and status/category filters. | Dashboard operations no longer load or render the entire index. |
| 4. Bounded AI processing | Add classification batch limits, retry/backoff, and usage reporting. Give the organizer indexed search and capped tool results and proposals. | Each AI operation has a defined scope and workload. |
| 5. Consistent organization rules | Let users review a folder scheme, preserve protected project structures, and reuse the scheme across batches. | Successive batches follow the same organization policy. |
| 6. Incremental scanning | Reuse hashes when size and modification time are unchanged; retain an explicit full-verification scan. | Routine rescans avoid rereading unchanged contents, with the tradeoff clearly documented. |
| 7. Large-folder validation | Benchmark synthetic folders with thousands of files, large documents, permission failures, and interrupted jobs. | Performance is measured and recovery tested before recommending whole-Desktop use. |

Add focused tests with each step. The final benchmark validates the combined
workflow rather than replacing those tests.

## Current Progress

- Step 1: complete — inventory and saved scope selection.
- Step 2: complete — background worker, persisted job status/results, progress,
  cooperative cancellation, restart recovery, and resumable processing.
- Step 3: complete — paginated library/search results, status/category filters,
  scoped SQL counts, bounded proposal pages, and metadata-only projections.
- Steps 4–7: remaining. Next is bounded AI processing.

Step 2 uses one background worker and keeps long operations out of HTTP request
handling. Completed classifications and generated proposals are persisted.
Cancelled scans restart; index repair does not apply partial results. Organization
can restart from the saved index and pending proposals, but does not restore the
previous AI conversation. In-flight file work or AI requests must finish before
cancellation. Tests cover cancellation, recovery, persisted progress, and avoiding
repeat classification; a localhost HTTP check verified responsiveness while a
job was active.

## First Step: Choose What to Organize

**Status: implemented.** The dashboard now provides metadata-only inventory,
top-level folder and loose-file selection, visible exclusions, project flags,
and persisted scope via migration 4. Saved scopes constrain reconciliation,
classification, search, agent tools, and move validation. Focused tests cover
metadata-only inspection, incomplete inventory, exclusion pruning, persistence,
and preservation of excluded indexed records. Background jobs were subsequently
added in Step 2. Nested folder selection is not part of this implementation.

### User experience

After entering a folder, the user sees a metadata-only inventory showing file
counts and total bytes by subfolder. Files directly inside the selected root
appear as a separate selection.

Illustrative inventory, not measured results:

```text
Desktop                         6,142 files
☑ Documents                       380 files
☑ Loose files                      42 files
☐ Projects                      4,900 files
☐ Other folders                   820 files
```

The user selects the desired scope and chooses **Use this scope**. This saves a
reusable workspace configuration and establishes the boundaries for scanning,
classification, search, and organization.

### Requirements

- Count files and total bytes using metadata without hashing files, extracting
  contents, or calling AI.
- Allow selection of subfolders and files directly inside the selected root.
- Exclude `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, and application
  bundles by default. Make the exclusions visible to the user.
- Flag detected project folders for deliberate inclusion rather than treating
  their contents as independent documents ready for organization.
- Display unreadable locations and identify incomplete counts.
- Persist the selection as a reusable workspace configuration.
- Apply the saved scope consistently across scanning, classification, search,
  and organization.

### Reconciliation correctness

Excluded files must not become “missing” during reconciliation. Previously
indexed files outside the new scope must retain their records and be omitted
from that workspace's operations.

Changing scope must not be interpreted as a filesystem deletion.

### Acceptance test

Create a Desktop-like test fixture containing:

- Standalone documents and files directly inside the root.
- A repository and dependency directories.
- An application bundle.
- An unreadable folder.
- Previously indexed files that are excluded by a changed scope.

Verify that the inventory explains what is included and excluded, performs no
content reads or AI calls, reports incomplete counts, and ensures subsequent
operations respect the saved scope. Confirm that excluded indexed files are
not marked missing.

### Rationale

Scope selection reduces unnecessary work before more expensive processing
begins. It also reduces the risk of reorganizing files whose existing folder
structure is necessary for a project or application to function.

## Principles to Preserve

- AI proposes moves; deterministic code validates and executes them.
- Every move still requires explicit user approval.
- Scanning and search remain separate from operations that send contents to AI.
- Incomplete scans must not produce false missing-file conclusions.
- Large-folder readiness must be supported by measurements, not inferred from
  small-folder tests.
