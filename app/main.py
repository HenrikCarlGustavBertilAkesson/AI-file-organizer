from scanner import scan_directory
from extractor import extract_text
from database import create_database, save_files
from ai.classifier import classify_file
from models import File


def main():

    FILE_READ_LIM = 10

    create_database()

    directory = input("Enter directory to scan: ")

    files = scan_directory(directory)

    print(f"\nFound {len(files)} files.\n")

    for file in files:
        print(f"Reading: {file.filename}")

        text = extract_text(file.path)

        file.content = text

        # Only send files with readable content to the AI
        if text.strip():

            FILE_READ_LIM -= 1
            if FILE_READ_LIM <= 0:
                print("Read limit reached.. exiting")
                break

            print("Classifying...")

            classification = classify_file(
                file.filename,
                file.extension,
                file.content,
            )

            file.category = classification.category
            file.subcategory = classification.subcategory
            file.description = classification.description
            file.confidence = classification.confidence

            print(
                f"  → {classification.category} "
                f"> {classification.subcategory} "
                f"({classification.confidence:.0%})"
            )

        else:
            print("  → No text available")

    save_files(files)

    print("\nDone!")


if __name__ == "__main__":
    main()