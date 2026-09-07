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
Use **Classify pending files** to extract and classify documents, search your
library, and **Suggest organization** to generate moves for individual approval.
Classification and organization use the AI API and require your configured key
and existing document-processing dependencies. The dashboard itself adds no
dependencies. It opens without an API key for scanning, indexing, and search.

The dashboard listens only on localhost. Keep the terminal running; stop with
Ctrl+C. Use `python3 app/web.py --port 8766` if the default port is occupied.
Operations run one at a time and show a waiting indicator. This first version
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
runs inventory synchronously, and does not yet implement background jobs or
incremental hashing from the scaling roadmap.

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
