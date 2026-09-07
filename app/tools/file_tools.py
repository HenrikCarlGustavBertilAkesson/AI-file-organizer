from scanner import scan_directory, scan_file
from extractor import extract_text
from database import get_all_files
from pathlib import Path
from ai.classifier import classify_file
from extractor import extract_text
from models import ProposedAction
from processor import process_file


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
    file = scan_file(path)

    processed_file = process_file(file)
    if isinstance(processed_file, dict):
        return {key: processed_file[key] for key in (
            "path", "category", "subcategory", "description", "confidence", "status"
        )}

    return {
        "path": processed_file.path,
        "category": processed_file.category,
        "subcategory": processed_file.subcategory,
        "description": processed_file.description,
        "confidence": processed_file.confidence,
        "status": processed_file.status,
    }

def propose_move(source: str, destination: str, reason: str) -> ProposedAction:
    return ProposedAction(
        action_type= "move",
        source=source,
        destination=destination,
        reason=reason
    )
