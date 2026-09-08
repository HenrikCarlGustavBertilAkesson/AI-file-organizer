from extractor import extract_text, is_supported
from database import get_file_by_path, save_file
from ai.classifier import classify_file
from models import File, ActionStatus
from ai.runtime import AILimitReached
from jobs import JobCancelled


def process_file(file: File):
    print(f"\nProcessing: {file.filename}")

    existing = get_file_by_path(file.path)

    # File hasn't changed since last classification
    if (
        existing
        and existing["hash"] == file.hash
        and existing["status"] == "classified"
    ):
        print("  → Already classified. Skipping.")

        return existing

    # Unsupported file format
    if not is_supported(file.path):
        print("  → Unsupported file type.")

        file.status = ActionStatus.UNSUPPORTED
        file.error = None

        save_file(file)

        return file

    try:
        content = extract_text(file.path)

        file.content= content

        # Extraction produced no usable text
        if not content.strip():
            print("  → No readable text.")

            file.status = ActionStatus.EMPTY
            file.error = None

            save_file(file)

            return file

        print(
            f"  → Extracted {len(content):,} characters."
        )

        classification = classify_file(
            filename=file.filename,
            extension=file.extension,
            content=content,
        )

        file.category = classification.category
        file.subcategory = classification.subcategory
        file.description = classification.description
        file.confidence = classification.confidence

        file.status = ActionStatus.CLASSIFIED
        file.error = None

        save_file(file)

        return file

    except (AILimitReached, JobCancelled):
        raise
    except Exception as error:
        print(f"  → FAILED: {error}")

        file.status = ActionStatus.FAILED
        file.error = str(error)

        save_file(file)

        return file
