from scanner import scan_directory
from extractor import extract_text
from database import create_database, save_files
from ai.classifier import classify_file
from models import File
from processor import process_file


def main():

    FILE_READ_LIM = 10

    create_database()

    directory = input("Enter directory to scan: ")

    print(f"\nScanning: {directory}")

    files = scan_directory(directory)

    print(f"Found {len(files)} files.")

    for file in files:

        FILE_READ_LIM -= 1
        if FILE_READ_LIM <= 0:
            print("Read limit reached.. exiting")
            break

        process_file(file)

    print("\nProcessing complete.")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()