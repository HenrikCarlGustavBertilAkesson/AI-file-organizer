# AI File Organizer

An agentic local file organizer that combines AI reasoning with deterministic, safety-checked filesystem operations.

The project is designed around a strict separation of responsibilities:

```text
AI recommendation
      ↓
Proposed action
      ↓
Deterministic validation
      ↓
Explicit user approval
      ↓
Filesystem operation
```

The AI can inspect, classify, and recommend actions, but it does not directly move or delete files.

## Goals

The long-term goal is to build a local file assistant that can:

- Scan directories and collect file metadata
- Extract text from supported document formats
- Classify files using an LLM
- Maintain a searchable SQLite index
- Inspect files through agent tools
- Propose sensible organization actions
- Require explicit approval before filesystem changes
- Persist proposed actions and their status
- Detect filesystem changes made outside the application
- Eventually support semantic search, deletion, duplicate detection, undo/history, and richer file understanding

## Current Features

### File scanning

The scanner discovers files recursively and records metadata such as:

- Path
- Filename
- Extension
- Size
- Modified timestamp
- SHA-256 hash

Internal file data is represented using a `File` dataclass.

### Text extraction

Text extraction currently supports:

- PDF
- DOCX
- TXT
- CSV
- XLSX

Unsupported files are still indexed, even when their contents cannot be extracted.

### AI classification

Supported files can be classified into useful logical categories such as:

- Finance
- Work
- Personal
- Legal
- Education
- Travel
- Programming
- Taxes
- Receipts
- Other

Classification includes:

- Category
- Subcategory
- Description
- Confidence score

Large file contents are truncated before being sent to the model.

### SQLite index

The application maintains a local SQLite database containing indexed file metadata, extracted text, classification data, processing status, hashes, and file presence state.

The database also stores proposed filesystem actions.

### Agent tools

The AI agent can use restricted tools to:

- List files
- Read extractable file contents
- Inspect indexed files
- Classify a path
- Propose a move

The agent cannot directly execute filesystem changes.

### Persistent action queue

Move proposals are stored in SQLite with statuses such as:

```text
pending
approved
rejected
executed
failed
```

The user can approve, reject, or skip each action.

Skipped actions remain pending.

### Safe move execution

Before a move is executed, a deterministic validator checks that:

- The action type is supported
- The source is inside the allowed directory
- The destination is inside the allowed directory
- The source exists
- The source is a file
- Source and destination differ
- The destination does not already exist

The user's allowed root directory is normalized once at the input boundary.

Agent-provided source and destination paths are still treated as untrusted input and validated separately.

### File index updates

When an approved move succeeds, the application updates the corresponding indexed file path in SQLite.

This preserves existing classification and metadata while keeping the database aligned with the filesystem.

## Safety Model

The central safety principle is:

```text
AI → proposal → validation → approval → deterministic execution
```

The LLM should never receive unrestricted filesystem access.

Destructive or filesystem-changing actions should always pass through deterministic application code.

The current design also limits organization operations to a user-selected root directory.

## Project Structure

The project currently resembles:

```text
ai-file-organizer/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── scanner.py
│   ├── extractor.py
│   ├── database.py
│   ├── models.py
│   ├── processor.py
│   ├── review.py
│   ├── reconciliation.py
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   └── classifier.py
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   └── organizer.py
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   └── file_tools.py
│   │
│   └── actions/
│       ├── __init__.py
│       ├── validator.py
│       └── executor.py
│
├── files.db
├── requirements.txt
└── README.md
```

Some modules may still be evolving as the architecture is refined.

## Running the Project

### Local browser dashboard

From the project directory, activate your existing environment and start:

```bash
source .venv/bin/activate
python3 app/web.py
```

Open **http://127.0.0.1:8765**. Paste a folder path and choose **Preview folder**.
Review the inventory, select the folders you want, and choose **Use this scope**.
Then choose **Scan for changes**, review the report, and **Update index**.
Use **Classify pending files** to extract and classify documents and search your
library. Review and save the category destinations under **Review organization
rules**, then use **Suggest organization** to generate moves for individual approval.
Classification and organization use the AI API and require your configured key
and existing document-processing dependencies. The dashboard itself adds no
dependencies. It opens without an API key for scanning, indexing, and search.

The dashboard listens only on localhost. Keep the terminal running; stop with
Ctrl+C. Use `python3 app/web.py --port 8766` if the default port is occupied.
Long operations run as background jobs with saved progress. This first version
accepts a pasted folder path rather than a native folder picker.

Create and activate a virtual environment, install the dependencies, and make sure an OpenAI API key is available in the environment.

Example:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="your-key"
python3 app/main.py
```

Do not hardcode API keys or commit them to Git.

## Development Status

### Incremental scanning and full verification

Reconciliation now defaults to a **quick scan**. It reuses a present file's stored
SHA-256 hash only when its path, size, and stored modification time match and the
stored hash is valid. New files, files with changed metadata, returning missing
files, and files without a valid hash are read and hashed. Excluded locations
remain excluded in both modes.

For a full content check, enable **Full verification** in the dashboard or run:

```bash
python3 app/reconciliation.py ~/Documents --full-verification
python3 app/reconciliation.py ~/Documents --full-verification --apply
```

Full verification rereads every included file. Quick scans can miss same-size
content changes with an unchanged modification timestamp (at the precision
stored in the index). They are a performance tradeoff, not proof that contents
are identical. Reports show the scan mode and counts of files hashed and hashes
reused. The dashboard uses the preview's mode when applying index repairs; job
resumption retains the selected mode.

Timestamp-only changes with identical hashes are reported as metadata-only
updates. Applying them refreshes size/time without clearing classification, so
future quick scans can reuse the hash. Preview remains read-only; without apply,
new or changed metadata is not cached for subsequent scans. No new database
schema is needed for this step.

Incomplete or cancelled scans never apply partial reconciliation results. A
file whose size, timestamps, or identity changes while hashing aborts the scan
so it can be retried. Neither mode provides an atomic snapshot of files being
actively edited. Classification still freshly scans files before processing.

### Consistent organization rules

Before starting AI organization, review the draft category-to-folder table and
choose **Save organization policy**. Each category maps to one folder relative
to the workspace, for example `Work → Documents/Contracts`. Edit the draft to
reuse existing destinations, remove unwanted categories, or add your own rules.
Migration 8 stores the reviewed policy and its revision for each workspace.
The same policy is used across batches and server restarts.

Destinations must stay within the saved scope. New subfolders under a selected
folder can be created during an approved move; saving rules itself creates no
folders and moves no files. If only loose files are selected, first create and
select a destination parent folder in the workspace scope. Rules do not silently
expand the scope.

The validator enforces the classified category's exact destination and preserves
the original filename. It rejects invented subfolders, renaming, and moves of
unclassified or unmapped files. Unknown categories stay in place until a rule is
added. Each move still needs approval and is revalidated immediately before
execution. Changing a rule can invalidate pending proposals; reject outdated
proposals before requesting replacements.

Add relative paths under **Additional protected folders** to preserve structures
that project detection cannot recognize. Detected project directories are always
protected against individual file moves, including moves into them. Protection
checks ancestor directories within the workspace for markers such as `.git`,
`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, and `.xcodeproj` entries.
This is a filename-based heuristic; custom protected paths cover other layouts.
Project markers are checked again at execution, not just during inventory.

Agent candidate queries omit already-organized, protected, unsupported/empty/failed,
unmapped classified files, and sources with pending proposals before pagination.
This allows successive batches to reach remaining eligible files. Ordinary file
search and classification continue to use the workspace scope; protection here
governs organization. Legacy manual proposals without a saved policy retain their
existing approval flow with project protection, while AI organization requires a
saved policy.

### Bounded AI batches and usage

Classification defaults to **25 files per run**, configurable from 1–100 in the
dashboard or with `--batch-size`:

```bash
python3 app/process_pending.py ~/Documents --batch-size 25
```

The batch uses pending files in the saved workspace, ordered by path; library
search filters do not select classification candidates. Remaining records stay
pending for another run. `--retry-failed` includes failed records. Unsupported
and empty files count toward the file limit but do not need a classification
request.

Organization defaults to **25 candidate files and 10 proposals**, configurable
up to 100 candidates and 50 proposals. Each run also has fixed upper limits:

- 15 model rounds and 40 function-tool calls.
- 45 API attempts shared by the organizer, nested classification, and retries.
- 20 rows per index-tool page and 12,000 characters per serialized tool result.
- 8,000 content characters returned by the read tool, and 120,000 characters of
  serialized conversation before another model request is allowed.
- 4,096 output tokens per organizer response and 2,048 per classification response.

The agent now has indexed keyword search and bounded index browsing. It does not
rescan/hash the workspace to list files. It can only read, classify, or propose
moves for candidates previously returned by an index tool in that run. All
tools still enforce the saved scope. Proposal validation and user approval are
unchanged. Limits are ceilings; a run may produce fewer results.

Transient API errors use up to two retries with exponential backoff and jitter.
Numeric Retry-After delays are respected up to 30 seconds; waiting checks for
cancellation. SDK retries are disabled so attempts are counted once. Requests
use a 60-second SDK timeout. Authentication and other permanent errors are not
retried. Classification permits at most three API attempts per selected file
across the run.

Migration 7 adds persisted job usage. The dashboard reports attempts, retries,
and provider-reported input/output tokens, including nested classifier calls.
Attempts without usage data are identified separately. These totals are not a
billing estimate; failed or interrupted requests may not return token usage.
Completed classifications and proposals survive cancellation or exhausted limits.
Resuming or starting another run grants a new bounded allowance.

API output caps and usage fields follow the
[Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
Tests use mocked API calls, including an installed-SDK structured-output check;
live model quality and large-folder throughput remain unbenchmarked.

### Paginated library and filters

The dashboard loads 50 files per page by default, with choices of 25 or 100.
Use **Previous** and **Next** to navigate. Status and category filters combine
with keyword search, and the count reflects all matching records, not just the
current page. **Show all** clears these filters. Missing records are available
through the status filter; the default view includes present files only.

Search results are ranked and paginated. Ordinary listings use stable path
ordering. Pending proposals are shown 20 per page with independent navigation.
Dashboard totals are calculated in SQLite. File rows omit extracted contents
and hashes, and descriptions are limited to 400 characters in the listing.
Reconciliation and processing request only their needed metadata columns.
Migration 6 adds indexes for library filtering and pending-action queries.

Counts still evaluate the saved scope across matching records. This change
bounds returned rows and browser rendering; it is not a completed large-folder
benchmark or the bounded AI workflow scheduled for Step 4.

### Background jobs and progress

Inventory, scanning, index repair, classification, and organization suggestions
run in a background worker. The dashboard remains available for viewing and
searching while a job runs. Only one background job runs at a time; changing
workspace scope and approving moves are blocked until it finishes.

The job card shows completed work, a total where known, failures, and current
activity. Inventory and scanning discover their totals as they run. Agent
progress reports rounds or its current tool, not an estimate of files organized.
Recent job status and results persist in SQLite through migration 5. Reloading
the browser reconnects to the most recent job for the saved folder.

Use **Cancel after current file** to stop at a safe boundary. An in-flight hash,
extraction, or AI request must finish before cancellation takes effect. Index
repair is atomic: cancellation is checked before its transaction, and a repair
already committing is allowed to finish. A cancelled scan never applies partial
results.

After server restart, unfinished jobs are marked interrupted. **Resume / restart**
creates a new job linked to the old one, using the current saved workspace scope:

- Classification continues with remaining pending files; successful results are
  already saved. The original retry-failed option is retained.
- Inventory and scans restart rather than reusing a potentially stale snapshot.
- Organization restarts its reasoning; proposals already generated are saved as
  pending and still require approval. AI conversation state is not resumed.

Run a single dashboard server for a database. Stopping the server requests
cooperative cancellation and waits for the current work to reach a safe boundary.
The CLI commands remain synchronous.

### Workspace scope and inventory

The dashboard previews file counts and total bytes for each top-level subfolder
and for files directly in the root. Inventory reads filesystem metadata only;
it does not hash files, extract text, or call AI. Counts omit excluded locations
and are marked incomplete if a location cannot be read.

Default excluded names are `.git`, `.venv`, `venv`, `node_modules`, and
`__pycache__`. You can edit these names before saving; each name applies at every
depth. Symbolic links and `.app`, `.bundle`, and `.framework` bundles are always
skipped within saved workspaces. The inventory lists skipped locations and errors.
Counts reflect the exclusions used for that preview; preview again after saving
changed exclusions to refresh them.

Folders containing recognized project markers start unchecked. This is a
filename-based heuristic, not a guarantee that every project is detected. A
selected top-level folder includes its descendants except excluded locations.
If a project is found deeper inside it, the whole top-level group is flagged
for deliberate inclusion.

Migration 4 stores a reusable scope for each exact normalized root in SQLite.
Scanning, classification, search, agent tools, and move validation use that saved
scope, including CLI operations invoked with the same root. Roots without a
saved scope retain their previous behavior. Preview the root again to change
its scope. Excluded indexed records remain stored and are not marked missing.

Move sources and destinations must both be in scope. Selecting only loose
files does not authorize moves into unselected subfolders; select the intended
destination folder as well. This first version selects top-level folders,
uses the incremental/full-verification modes described above.

### Keyword search

```bash
python3 app/search.py ~/Documents "employment contract"
python3 app/search.py ~/Documents "invoice" --limit 10
```

Search uses SQLite FTS5 to rank matches across filenames, extracted content,
categories, subcategories, and descriptions, with matching snippets. Every query
word must match; raw FTS operators are treated as ordinary words. Results include
only records marked present inside the selected root. Run reconciliation first
to detect files removed outside the application.

The search command opens SQLite read-only and makes no AI calls. Upgrade an older
database through normal application startup or reconciliation with `--apply`
before searching. Migration 3 backfills existing records and installs triggers
that synchronize the index on insert, update, and deletion. New or modified files
need pending-file processing before their extracted contents become searchable.

### Process pending files

After reconciliation queues new or modified files, run classification explicitly:

```bash
python3 app/process_pending.py /path/to/directory
python3 app/process_pending.py /path/to/directory --retry-failed
```

This command rescans present pending files inside the selected root and runs the
existing extraction/classification pipeline. Classification may send supported
file contents to the AI API; configure dependencies and the API key as above.
The retry option includes failed files. Missing files, paths resolving outside
the root, and already classified records are skipped. The summary counts
classified, unsupported, empty, failed, and skipped files. Individual file
failures do not stop the queue; failures produce a nonzero exit status.

### Database upgrades

Application startup automatically applies ordered SQLite migrations from
`app/migrations.py`, tracked with `PRAGMA user_version`. Existing unversioned
databases are adopted, including file indexes missing `hash`, `status`, `error`,
or `is_present`. Records and action history are preserved. All outstanding
migrations run in one transaction; a failed upgrade rolls back schema, data,
and version changes. Databases newer than this application supports are rejected.

The standalone reconciliation command also initializes/upgrades the database
when using `--apply`; its default preview does not run migrations. Schema upgrades
commit separately from reconciliation repairs.

For future schema changes, append a migration function to `MIGRATIONS` rather
than editing released migrations. Use `connection.execute()` within migrations;
do not commit or use `executescript()`, which can break transaction boundaries.

Run the automated checks with `python3 -m unittest discover -s tests -v`.

The project currently has working:

- File discovery
- Metadata and hashing
- Text extraction
- AI classification
- SQLite persistence
- Agent tool calling
- Persistent move proposals
- Approval flow
- Deterministic validation
- Safe execution
- Indexed path updates

Filesystem reconciliation is the next major area under development.

See `PROJECT_PLAN.md` for the architectural history and planned next steps.
