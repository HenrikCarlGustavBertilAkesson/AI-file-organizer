from scanner import scan_directory
from extractor import extract_text
from database import get_all_files
from pathlib import Path
from ai.classifier import classify_file
from extractor import extract_text
from models import ProposedAction

MAX_TOOL_CONTENT_LENGTH = 20_000

def list_files(directory: str) -> list[dict]:
    files = scan_directory(directory)

    return [
        {
            "path": file.path,
            "filename": file.filename,
            "extension": file.extension,
            "size": file.size,
            "modified": file.modified,
        }
        for file in files
    ]

def read_file(path: str) -> dict:
    content = extract_text(path)

    if not content.strip():
        return {
            "path": path,
            "content": "",
            "message": "No readable text could be extracted.",
        }

    if len(content) > MAX_TOOL_CONTENT_LENGTH:
        half = MAX_TOOL_CONTENT_LENGTH // 2

        content = (
            content[:half]
            + "\n\n--- CONTENT TRUNCATED ---\n\n"
            + content[-half:]
        )

    return {
        "path": path,
        "content": content,
    }

def get_indexed_files() -> list[dict]:
    files = get_all_files()

    return [
        {
            "path": file["path"],
            "filename": file["filename"],
            "category": file["category"],
            "subcategory": file["subcategory"],
            "description": file["description"],
            "confidence": file["confidence"],
            "status": file["status"],
        }
        for file in files
    ]

def classify_path(path: str) -> dict:
    file_path = Path(path)

    content = extract_text(path)

    if not content.strip():
        return {
            "path": path,
            "error": "No readable content found.",
        }

    classification = classify_file(
        filename=file_path.name,
        extension=file_path.suffix.lower(),
        content=content,
    )

    return {
        "path": path,
        "category": classification.category,
        "subcategory": classification.subcategory,
        "description": classification.description,
        "confidence": classification.confidence,
    }

def propose_move(source: str, destination: str, reason: str) -> ProposedAction:
    return ProposedAction(
        action_type= "move",
        source=source,
        destination=destination,
        reason=reason
    )