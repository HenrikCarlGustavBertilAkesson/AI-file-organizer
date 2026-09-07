# AI File Organizer — Project Plan

## 1. Purpose

The project is an agentic local file organizer that combines AI reasoning with deterministic filesystem tooling.

Its purpose is not simply to automatically move files. The goal is to build a system that can understand a user's local files, reason about sensible organization, maintain a reliable local index, and safely propose actions while keeping the user in control.

The key architectural principle is:

```text
AI → recommendation → deterministic validation → user approval → filesystem operation
```

The model may reason about files and recommend changes, but trusted Python code remains responsible for validation and execution.

---

# 2. Core Architecture

The system is being divided into several layers.

```text
Filesystem
    ↓
Scanner
    ↓
File dataclasses
    ↓
Extraction / processing
    ↓
SQLite index
    ↓
Agent tools
    ↓
AI reasoning
    ↓
ProposedAction
    ↓
Persistent action queue
    ↓
User review
    ↓
Validator
    ↓
Executor
    ↓
Filesystem
```

The boundaries are intentional.

The AI layer should not directly perform filesystem mutations.

---

# 3. Work Completed So Far

## Step 1 — Basic file scanner

The project started with a recursive local filesystem scanner using `pathlib`.

The scanner discovers files and records metadata such as:

- Absolute path
- Filename
- Extension
- Size
- Modification timestamp

The project was later standardized around a `File` dataclass rather than dictionaries for internal file representation.

Example concept:

```python
@dataclass
class File:
    path: str
    filename: str
    extension: str
    size: int
    modified: float
```

This provides a stable internal domain model.

---

## Step 2 — File hashing

SHA-256 hashing was added to the scanner.

Hashes provide a stable way to identify file contents independently of path.

This enables future capabilities including:

- Duplicate detection
- Change detection
- Manual move detection
- Avoiding unnecessary reprocessing
- Preserving metadata when a file moves

The scanner reads files in chunks rather than loading entire files into memory.

---

## Step 3 — Text extraction

A text extraction layer was added for common document formats.

Current supported formats include:

```text
.pdf
.docx
.txt
.csv
.xlsx
```

Libraries used include:

- `pypdf`
- `python-docx`
- `openpyxl`

The extractor safely returns empty content for unsupported files or files whose contents cannot be extracted.

Images are currently not visually interpreted.

This means image organization is mostly limited to metadata and filename reasoning for now.

---

## Step 4 — AI classification

An AI classifier was introduced using OpenAI structured output.

The classifier returns structured fields such as:

```text
category
subcategory
description
confidence
```

The model is instructed to classify files by logical purpose rather than file format.

For example:

```text
invoice.pdf
→ Finance / Invoice

resume.docx
→ Work / Resume
```

Large file contents are bounded before being sent to the model.

The application currently treats confidence as a useful heuristic rather than a mathematically calibrated probability.

---

## Step 5 — Processing pipeline

File scanning, extraction, classification, and persistence were gradually consolidated into a processing pipeline.

A file can move through statuses such as:

```text
classified
unsupported
empty
failed
```

A key design decision was made:

> Every scanned file should be represented in SQLite, even if it cannot be classified.

For example, a JPG without extractable text should still have a database record.

This makes the local file index describe the filesystem rather than only the subset of files understood by the AI.

---

## Step 6 — SQLite index

A local SQLite database was introduced.

The file index stores information such as:

- Path
- Filename
- Extension
- Size
- Modified timestamp
- SHA-256 hash
- Extracted content
- Category
- Subcategory
- Description
- Confidence
- Processing status
- Error state
- Presence state

An `is_present` field is being used or planned to distinguish files that still exist from files that have disappeared from disk.

The application uses an upsert-style pattern to update existing indexed files rather than blindly replacing records.

---

## Step 7 — Restricted AI tools

An agent tool layer was introduced.

The agent can use controlled functions such as:

```text
list_files
read_file
get_indexed_files
classify_path
propose_move
```

These tools expose only the capabilities the agent actually needs.

The internal `File` dataclass may be converted into JSON-compatible data at the model boundary, but dictionaries are not used as the core internal representation.

The agent does not receive direct access to arbitrary Python filesystem operations.

---

## Step 8 — Agentic organization

An organizer agent was created using function calling.

The agent can:

1. List files
2. Inspect indexed metadata
3. Read file content
4. Request classification
5. Reason about sensible organization
6. Propose moves

The agent operates iteratively until no more tool calls are required or a maximum step limit is reached.

The agent is explicitly instructed not to invent file paths or claim that moves have already occurred.

---

## Step 9 — ProposedAction model

Filesystem changes were formalized as domain objects.

Conceptually:

```python
@dataclass
class ProposedAction:
    action_type: str
    source: str
    destination: str
    reason: str
    id: int | None = None
    status: str = "pending"
    error: str = ""
```

Using a single status field avoids contradictory combinations such as:

```text
approved = False
executed = True
```

The intended state flow is:

```text
pending
   ├── rejected
   └── approved
          ├── executed
          └── failed
```

---

## Step 10 — Deterministic validation

A validator was added before filesystem execution.

It checks that:

- The action type is supported
- Source is inside the allowed root
- Destination is inside the allowed root
- Source exists
- Source is a file
- Source and destination are different
- Destination does not already exist

This validator is the true security boundary.

The AI prompt provides behavioral guidance, but security does not rely on the model obeying the prompt.

---

## Step 11 — Path normalization

A bug exposed an important path handling issue.

User input such as:

```text
~/desktop/test/testfolder
```

must use:

```python
Path(value).expanduser().resolve()
```

because `resolve()` alone does not expand `~`.

The architecture now follows this boundary:

```text
main.py
    normalize trusted user root once

validator.py
    independently normalize and validate
    untrusted agent-provided paths
```

This prevents repeated unnecessary normalization of the trusted root while still treating model-generated paths as untrusted.

---

## Step 12 — Explicit approval flow

The user must explicitly approve each proposed filesystem action.

The CLI supports decisions such as:

```text
yes
no
skip
```

Their meanings are:

```text
yes
→ approve and attempt execution

no
→ reject permanently

skip
→ leave pending for later review
```

No move is executed merely because the AI proposed it.

---

## Step 13 — Persistent action queue

Proposals were moved from temporary in-memory state into SQLite.

The actions table stores:

- ID
- Action type
- Source
- Destination
- Reason
- Status
- Error
- Created timestamp
- Updated timestamp

This provides:

- Persistence between runs
- Auditing
- Deferred review
- A foundation for undo/history
- A foundation for batch approval

Duplicate pending moves for the same source are also handled so competing proposals do not accumulate indefinitely.

---

## Step 14 — Safe execution

Approved actions are passed to a deterministic executor.

The executor:

1. Requires approved status
2. Re-runs validation
3. Creates destination directories when needed
4. Performs the move using Python
5. Marks the action as executed or failed

The validator runs again immediately before execution so assumptions made during review cannot silently become stale.

---

## Step 15 — Keeping the file index updated after moves

A successful move changes the actual path of a file.

The application now updates the corresponding row in the `files` table after successful execution.

For example:

```text
before:
.../tinytest/employment_contract.pdf

after:
.../tinytest/Work/Employment/employment_contract.pdf
```

Classification data is preserved because only the indexed path and filename need to change.

A test initially exposed that some files were not indexed before moves.

The processing flow was adjusted so all scanned files, including unsupported ones, enter SQLite.

The path-update tests now pass.

---

# 4. Current Development Point

The project is now ready to implement filesystem reconciliation.

At this point the application correctly handles changes it performs itself.

The remaining problem is changes made outside the application.

For example, a user might manually:

- Add a file in Finder
- Delete a file
- Rename a file
- Move a file between folders

SQLite will not automatically know that happened.

Reconciliation will solve that.

---

# 5. Next Step — Read-Only Filesystem Reconciliation

The first reconciliation implementation should only observe and report changes.

It should:

1. Scan the allowed root
2. Read indexed files from SQLite
3. Compare filesystem paths with indexed paths
4. Report:
   - New files
   - Missing files
   - Probable moved files

Initially it should not automatically modify the database.

This makes it possible to verify the detection logic safely.

Example:

```text
SQLite:
A
B
C

Filesystem:
B
C
D
```

Reconciliation would report:

```text
Missing:
A

New:
D
```

---

# 6. Hash-Based Manual Move Detection

Path comparison alone cannot distinguish:

```text
file deleted
+
different file created
```

from:

```text
same file manually moved
```

SHA-256 solves much of this problem.

If:

```text
old path hash = ABC123
new path hash = ABC123
```

then the application can infer that the file was probably moved.

The reconciliation layer should therefore match missing indexed files against new scanned files by hash.

Expected result:

```text
old/path/report.pdf
    ↓
new/path/report.pdf
```

instead of reporting one missing file and one new file.

Duplicate hashes must be handled carefully.

If multiple candidates have the same hash, the application should avoid guessing.

---

# 7. Automatic Reconciliation

After read-only reconciliation has been thoroughly tested, the next stage should allow deterministic database repair.

Expected behavior:

```text
New file
→ process and index it

Missing file
→ mark is_present = False

Detected move
→ update existing File.path
```

This should not involve AI reasoning.

Reconciliation is a deterministic filesystem/database consistency task.

---

# 8. Database Migrations

The project has so far been evolving quickly enough that SQLite columns have sometimes been added manually.

As the schema stabilizes, introduce proper database migrations.

This will prevent development steps from relying on:

```text
rm files.db
```

or one-off manual `ALTER TABLE` commands.

Potential options include:

- A small custom migration table
- Alembic if the project later adopts SQLAlchemy

A simple migration mechanism is likely sufficient initially.

---

# 9. Semantic Search

After the filesystem/index consistency layer is reliable, add semantic search.

The goal should be queries such as:

```text
find my employment contracts

show invoices related to broadband

find documents about my 2025 taxes

show travel documents for Spain
```

Likely architecture:

```text
File content
    ↓
chunking
    ↓
embeddings
    ↓
vector index
    ↓
semantic retrieval
    ↓
agent
```

SQLite can continue to store structured metadata while a vector-capable layer handles semantic similarity.

Before adding a separate vector database, investigate whether a lightweight local approach is sufficient.

---

# 10. Search Tool for the Agent

Once search exists, expose it through a constrained agent tool such as:

```text
search_files(query)
```

The agent should retrieve candidate files rather than scanning the entire filesystem for every question.

This will make requests such as:

```text
Find all documents related to my previous employer.
```

much more efficient.

---

# 11. Image Understanding

Images currently have no extractable textual content.

A later stage can add vision-based analysis for image files.

Potential outputs:

```text
category
description
objects
location hints
document/photo distinction
```

For example:

```text
badrum.jpg
→ Photos / Home
→ Bathroom interior photo
```

instead of relying on the Swedish filename `badrum`.

This should remain optional because sending images to an external API has privacy and cost implications.

---

# 12. Duplicate Detection

Existing hashes provide most of the foundation.

Files with identical SHA-256 hashes can be identified as exact duplicates.

The system could later propose:

```text
These two files are identical.

Keep:
/Documents/report.pdf

Possible duplicate:
/Downloads/report (1).pdf
```

Deletion should require a stronger safety flow than ordinary organization moves.

The first version should only report duplicates.

---

# 13. Action History and Undo

The persistent actions table provides the basis for an undo system.

A successful move already records:

```text
source
destination
```

An undo operation can therefore propose:

```text
destination
→
source
```

Undo should still use:

```text
proposal
→ validation
→ approval
→ execution
```

rather than bypassing the safety model.

Additional action metadata may eventually include:

- Executed timestamp
- Undo status
- Parent action ID
- File hash before execution

---

# 14. Batch Review

Once individual approval is stable, support workflows such as:

```text
Approve all 14 low-risk organization moves
```

The UI should still show the proposed changes before execution.

Useful capabilities could include:

- Approve all
- Reject all
- Approve selected
- Filter by confidence
- Filter by target folder

Each action should still be validated independently before execution.

---

# 15. Additional Filesystem Actions

Moves should remain the only mutation until the architecture is proven reliable.

Possible later action types include:

```text
rename
copy
create_folder
archive
delete
```

Deletion should be introduced last and should use stronger safeguards.

Possible deletion safety features:

- Trash instead of permanent deletion
- Explicit deletion confirmation
- Hash verification
- Action logging
- Undo window

---

# 16. Privacy Improvements

The current classifier may send extracted local file contents to an external AI API.

Before using the organizer on sensitive directories, consider privacy controls such as:

- Excluded directories
- Excluded extensions
- Maximum file sizes
- Local-only mode
- Redaction
- User-selected files
- Local models
- Clear logging of what content is sent externally

The application should make this boundary visible rather than silently uploading arbitrary local files.

---

# 17. Agent Efficiency

The current agent can perform several exploratory tool calls.

Later improvements can reduce unnecessary calls by allowing it to query the local index first.

Preferred future pattern:

```text
User request
    ↓
indexed search
    ↓
small candidate set
    ↓
read only relevant files
    ↓
reason
```

rather than:

```text
scan everything
→ read everything
→ classify everything
```

This will reduce latency and API cost.

---

# 18. Testing

As the project grows, automated tests should cover the deterministic layers heavily.

Priority test areas:

### Scanner

- Recursive scanning
- Hidden files
- Permission failures
- Hash correctness
- Path normalization

### Validator

- Paths inside root
- Paths outside root
- `..` traversal
- Destination collisions
- Missing source
- Same source/destination

### Executor

- Approved move
- Unapproved action
- Failed move
- Directory creation

### Database

- File upsert
- Action persistence
- Status transitions
- Path updates
- Duplicate pending actions

### Reconciliation

- New file
- Missing file
- Manual move
- Duplicate hashes
- File modified in place

The AI layer should have fewer brittle tests than deterministic application logic.

---

# 19. Likely Near-Term Roadmap

A reasonable sequence from the current state is:

```text
1. Read-only reconciliation
        ↓
2. Hash-based move detection
        ↓
3. Automatic index repair
        ↓
4. Database migrations
        ↓
5. Automated tests for filesystem safety
        ↓
6. Semantic search
        ↓
7. Agent search tool
        ↓
8. Image understanding
        ↓
9. Duplicate detection
        ↓
10. Action history + undo
        ↓
11. Batch approval
        ↓
12. Additional safe action types
```

The ordering matters.

The project should become reliable before it becomes more autonomous.

---

# 20. Architectural Principles to Preserve

As development continues, preserve these principles.

## AI proposes; deterministic code executes

Never give the model unrestricted access to `shutil`, shell commands, or arbitrary local filesystem operations.

## Treat model output as untrusted

Paths, destinations, action types, and arguments produced by the agent should always be validated.

## Keep internal models typed

Use dataclasses such as `File`, `ProposedAction`, and `AgentResult` internally.

Only convert to dictionaries/JSON at external boundaries.

## Prefer local deterministic state

SQLite should remain the source of truth for indexed metadata and action history.

## Fail safely

When the application is uncertain, it should:

```text
report
skip
ask for approval
```

rather than make an irreversible guess.

## Keep destructive actions last

Organization moves are relatively easy to recover from.

Deletion is not.

Do not add automatic deletion until the rest of the architecture is mature and thoroughly tested.

---

# 21. Definition of the Project's Next Milestone

The next major milestone is reached when:

1. A file added manually is detected and indexed.
2. A file deleted manually is marked missing.
3. A file moved manually inside the allowed root is recognized by hash.
4. The existing database record follows that move.
5. No AI call is required for basic filesystem/database reconciliation.

At that point, SQLite will be able to maintain an accurate representation of the user's directory even when changes happen outside the application.

That creates the reliable foundation needed for semantic search and increasingly capable agent behavior.
