#!/usr/bin/env python3
"""
This tool splits a large folder into smaller "Disc" folders based on a size limit (default 4.6GB for DVDs). 
It preserves your folder structure and generates SHA256 checksums for each disc to verify that no data is corrupted after burning.

Usage: python script.py <source_folder> <output_folder> [--capacity bytes] [--plan-only]
"""
from pathlib import Path
from collections import defaultdict
import argparse
import hashlib
import os
import shutil
import sys


DEFAULT_CAPACITY = 4_600_000_000
DVD_SECTOR_SIZE = 2048

INTEGRITY_FOLDER_NAME = "Check data integrity on disk"
CHECKSUM_FILENAME = "checksums.sha"
INSTRUCTIONS_FILENAME = "Verification instructions.txt"

INSTRUCTIONS_TEXT = """Verification Instructions

This disc contains a file named checksums.sha. This file is used to ensure that your data has not been corrupted over time or damaged during the burning process.

How to verify your files on Linux:

1. Open a terminal.
2. Navigate to the folder containing the checksums.sha file.
3. Enter the following command:

   sha256sum -c checksums.sha --quiet

What to expect:
- If the command finishes without printing any messages, all files are intact and correct.
- If any files are damaged or missing, the terminal will list those specific files.

Please note:
- This process detects corruption; it cannot repair damaged files.
- To keep the verification working, do not rename or move the files on the disc.

To create a new checksum file for a folder, use:

find . -type f ! -name 'checksums.sha' -print0 | sort -z | xargs -0 sha256sum > checksums.sha
"""


def sha256_file(file_path, chunk_size=1024 * 1024):
    """Calculate SHA256 checksum of a file."""
    digest = hashlib.sha256()

    with file_path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def alphabetical_key(path):
    return str(path).casefold()


def find_top_level_folders(source_folder):
    folders = [
        path
        for path in source_folder.iterdir()
        if path.is_dir() and not path.is_symlink()
    ]

    return sorted(
        folders,
        key=lambda path: path.name.casefold()
    )


def find_files(top_level_folder):
    files = [
        path
        for path in top_level_folder.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]

    return sorted(
        files,
        key=lambda path: alphabetical_key(
            path.relative_to(top_level_folder)
        )
    )


def create_disc_plan(top_level_folders, capacity):
    """Create a plan for distributing files across discs."""
    assignments = []

    disc_number = 1
    used_space = 0

    for top_level_folder in top_level_folders:
        files = find_files(top_level_folder)

        folder_size = sum(
            file_path.stat().st_size
            for file_path in files
        )

        for file_path in files:
            file_size = file_path.stat().st_size

            if file_size > capacity:
                raise ValueError(
                    "A single file is larger than the selected capacity:\n"
                    f"  File: {file_path}\n"
                    f"  Size: {file_size:,} bytes\n"
                    f"  Capacity: {capacity:,} bytes"
                )

        if used_space > 0:
            if folder_size <= capacity:
                if used_space + folder_size > capacity:
                    disc_number += 1
                    used_space = 0

            else:
                disc_number += 1
                used_space = 0

        for file_path in files:
            file_size = file_path.stat().st_size

            if (
                used_space > 0
                and used_space + file_size > capacity
            ):
                disc_number += 1
                used_space = 0

            assignments.append(
                {
                    "top_level_name": top_level_folder.name,
                    "top_level_folder": top_level_folder,
                    "file": file_path,
                    "disc": disc_number,
                    "size": file_size,
                }
            )

            used_space += file_size

    return assignments


def calculate_folder_parts(assignments):
    """Determine how many parts each folder is split across."""
    discs_by_folder = defaultdict(list)

    for item in assignments:
        folder_name = item["top_level_name"]
        disc = item["disc"]

        if disc not in discs_by_folder[folder_name]:
            discs_by_folder[folder_name].append(disc)

    for discs in discs_by_folder.values():
        discs.sort()

    result = {}

    for folder_name, discs in discs_by_folder.items():
        total_parts = len(discs)

        for part_number, disc in enumerate(discs, start=1):
            result[(folder_name, disc)] = (
                part_number,
                total_parts
            )

    return result


def get_output_folder_name(folder_name, disc, folder_parts):
    """Get the output folder name, accounting for multi-part folders."""
    part_number, total_parts = folder_parts[(folder_name, disc)]

    if total_parts == 1:
        return folder_name

    return f"{folder_name} ({part_number} out of {total_parts})"


def print_disc_plan(assignments, capacity):
    """Display the planned disc distribution."""
    disc_sizes = defaultdict(int)
    disc_file_counts = defaultdict(int)

    for item in assignments:
        disc = item["disc"]
        disc_sizes[disc] += item["size"]
        disc_file_counts[disc] += 1

    if not disc_sizes:
        print("No files were found.")
        return

    print("\nDisc plan")
    print("---------")

    for disc in sorted(disc_sizes):
        size = disc_sizes[disc]
        file_count = disc_file_counts[disc]
        percentage = size / capacity * 100

        print(
            f"Disc {disc:02d}: "
            f"{size:,} bytes, "
            f"{file_count} files, "
            f"{percentage:.1f}% full"
        )

    print(f"\nTotal discs: {max(disc_sizes)}")
    print(f"Planning capacity: {capacity:,} bytes")


def print_output_structure(assignments):
    """Display the resulting folder structure."""
    folder_parts = calculate_folder_parts(assignments)
    folders = set()

    for item in assignments:
        folder_name = item["top_level_name"]
        source_top_level_folder = item["top_level_folder"]
        source_file = item["file"]
        disc = item["disc"]

        output_top_level_name = get_output_folder_name(
            folder_name,
            disc,
            folder_parts
        )

        disc_folder = Path(f"Disc {disc:02d}")

        destination_file = (
            disc_folder
            / output_top_level_name
            / source_file.relative_to(source_top_level_folder)
        )

        folders.add(disc_folder)

        for parent in destination_file.parents:
            if str(parent) != ".":
                folders.add(parent)

    print("\nResulting folder structure")
    print("--------------------------")

    for folder in sorted(
        folders,
        key=lambda path: str(path).casefold()
    ):
        print(f"  {folder}\\")


def copy_files(assignments, output_folder):
    """Copy files to disc folders with clean progress display."""
    folder_parts = calculate_folder_parts(assignments)
    total_files = len(assignments)
    current_disc = None
    max_filename_width = 50  # Truncate long filenames to avoid line wrapping

    print("\nCopying files to discs...")

    for index, item in enumerate(assignments, start=1):
        folder_name = item["top_level_name"]
        source_top_level_folder = item["top_level_folder"]
        source_file = item["file"]
        disc = item["disc"]

        # Print disc header when switching to a new disc
        if disc != current_disc:
            if current_disc is not None:
                print()  # Newline after previous disc progress
            print(f"\nDisc {disc:02d}:")
            current_disc = disc

        output_top_level_name = get_output_folder_name(
            folder_name,
            disc,
            folder_parts
        )

        disc_folder = output_folder / f"Disc {disc:02d}"
        output_top_level_folder = (
            disc_folder / output_top_level_name
        )

        relative_path = source_file.relative_to(
            source_top_level_folder
        )

        destination_file = output_top_level_folder / relative_path
        destination_file.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        shutil.copy2(source_file, destination_file)

        # Update progress on same line with proper clearing
        percentage = index / total_files * 100
        file_name = source_file.name

        # Truncate filename if too long
        if len(file_name) > max_filename_width:
            file_name = file_name[:max_filename_width - 3] + "..."

        # Use ANSI escape code to clear to end of line
        progress_line = (
            f"  [{index:5d}/{total_files}] {percentage:5.1f}% — {file_name}"
        )
        print(f"\r{progress_line}\033[K", end="", flush=True)

    print()  # Final newline
    print(f"\n✓ Finished copying {total_files} files to:\n  {output_folder}")


def create_checksums_for_disc(disc_folder):
    """Create checksums.sha and verification instructions for a disc."""
    integrity_folder = disc_folder / INTEGRITY_FOLDER_NAME
    instructions_file = integrity_folder / INSTRUCTIONS_FILENAME
    checksum_file = integrity_folder / CHECKSUM_FILENAME

    integrity_folder.mkdir(parents=True, exist_ok=True)

    with instructions_file.open(
        "w",
        encoding="utf-8",
        newline="\n"
    ) as file:
        file.write(INSTRUCTIONS_TEXT)

    files = []

    for file_path in disc_folder.rglob("*"):
        if not file_path.is_file() or file_path.is_symlink():
            continue

        if file_path == checksum_file:
            continue

        relative_path = Path(
            os.path.relpath(file_path, integrity_folder)
        ).as_posix()

        if "\n" in relative_path:
            raise ValueError(
                "Unsupported filename containing a newline:\n"
                f"  {file_path}"
            )

        files.append((file_path, relative_path))

    files.sort(key=lambda item: item[1].casefold())

    total_files = len(files)

    print(f"\n{disc_folder.name}:")
    print(f"  Total files: {total_files}")

    with checksum_file.open(
        "w",
        encoding="utf-8",
        newline="\n"
    ) as output:
        for index, (file_path, relative_path) in enumerate(files, start=1):
            digest = sha256_file(file_path)

            output.write(f"{digest}  {relative_path}\n")

            percentage = index / total_files * 100 if total_files else 100
            file_name = file_path.name

            # Truncate filename if too long
            if len(file_name) > 50:
                file_name = file_name[:47] + "..."

            # Use ANSI escape code to clear to end of line
            progress_line = (
                f"  [{index:5d}/{total_files}] {percentage:5.1f}% — {file_name}"
            )
            print(f"\r{progress_line}\033[K", end="", flush=True)

    print()  # Final newline
    print(f"  ✓ Created: {checksum_file.name}")


def find_disc_folders(output_folder):
    """Find all Disc XX folders."""
    disc_folders = []

    for path in output_folder.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue

        if path.name.startswith("Disc "):
            disc_folders.append(path)

    return sorted(
        disc_folders,
        key=lambda path: path.name.casefold()
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Organize files into disc-sized folders with checksums "
            "and verification instructions."
        )
    )

    parser.add_argument(
        "source_folder",
        type=Path,
        help="Folder containing the top-level folders to organize"
    )

    parser.add_argument(
        "output_folder",
        type=Path,
        nargs="?",
        help=(
            "Folder where Disc 01, Disc 02, etc. will be created. "
            "Not required when --plan-only is used."
        )
    )

    parser.add_argument(
        "--capacity",
        type=int,
        default=DEFAULT_CAPACITY,
        help=(
            "Maximum size per disc in bytes. "
            f"Default: {DEFAULT_CAPACITY:,}"
        )
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "Show the disc plan and resulting folder structure "
            "without copying files or creating checksums."
        )
    )

    parser.add_argument(
        "--skip-checksums",
        action="store_true",
        help="Copy files to discs but skip checksum generation."
    )

    args = parser.parse_args()

    source_folder = args.source_folder.expanduser().resolve()

    if args.output_folder is not None:
        output_folder = args.output_folder.expanduser().resolve()
    else:
        output_folder = None

    if not args.plan_only and output_folder is None:
        parser.error(
            "output_folder is required unless --plan-only is used"
        )

    if not source_folder.exists():
        print(f"Source folder does not exist:\n{source_folder}")
        sys.exit(1)

    if not source_folder.is_dir():
        print(f"Source path is not a folder:\n{source_folder}")
        sys.exit(1)

    if args.capacity <= 0:
        print("Capacity must be greater than zero.")
        sys.exit(1)

    if output_folder is not None:
        try:
            output_folder.relative_to(source_folder)

            print(
                "The output folder must not be inside the source folder."
            )
            sys.exit(1)

        except ValueError:
            pass

    top_level_folders = find_top_level_folders(source_folder)

    if not top_level_folders:
        print("No top-level folders were found.")
        sys.exit(1)

    print("Top-level folders found:")

    for folder in top_level_folders:
        print(f"  {folder.name}")

    try:
        assignments = create_disc_plan(
            top_level_folders,
            args.capacity
        )

    except ValueError as error:
        print(f"\n✗ Error: {error}")
        sys.exit(1)

    print_disc_plan(assignments, args.capacity)
    print_output_structure(assignments)

    if args.plan_only:
        print("\n(Plan only — no files copied)")
        return

    if output_folder.exists():
        print(f"\n⚠ Output folder already exists:\n  {output_folder}")
        response = input("Continue anyway and overwrite? (yes/no): ").strip().lower()

        if response != "yes":
            print("Cancelled.")
            sys.exit(0)

    try:
        output_folder.mkdir(parents=True, exist_ok=True)
        copy_files(assignments, output_folder)

    except Exception as error:
        print(f"\n✗ Error copying files: {error}")
        sys.exit(1)

    if args.skip_checksums:
        print("\n(Skipping checksum generation)")
        return

    print("\nGenerating checksums...")

    disc_folders = find_disc_folders(output_folder)

    for disc_folder in disc_folders:
        create_checksums_for_disc(disc_folder)

    print(
        "\n✓ All discs completed successfully!"
        f"\n  Output folder: {output_folder}"
    )


if __name__ == "__main__":
    main()

