#!/usr/bin/env python3
from pathlib import Path
from collections import defaultdict
import argparse
import hashlib
import os
import shutil
import sys

# Target ~4.6 GB (safe boundary slightly under standard 4.7 GB DVD-R capacity)
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
    """Compute the SHA-256 hash of a file reading in chunks to prevent memory bloat."""
    digest = hashlib.sha256()
    with file_path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()

def sort_key_case_insensitive(path):
    """Provide a consistent, case-insensitive sort key across different operating systems."""
    return str(path).casefold()

def bytes_to_gb(bytes_value):
    """Convert raw byte counts into human-readable gigabytes."""
    return bytes_value / (1024 ** 3)

class ProgressTracker:
    """Track and render a clean, 3-line live dashboard updated in place."""

    def __init__(self, disc_number, capacity, total_files, cumulative_start=0):
        self.disc_number = disc_number
        self.capacity = capacity
        self.total_files = total_files
        self.cumulative_file_index = cumulative_start
        self.disc_bytes_written = 0
        self.current_folder = None
        self.has_drawn = False

    def update(self, file_size, folder_name, filename):
        """Redraw the 3-line block in place without spilling or wrapping."""
        self.cumulative_file_index += 1
        self.disc_bytes_written += file_size
        self.current_folder = folder_name

        term_width = shutil.get_terminal_size(fallback=(80, 24)).columns

        # Calculate metrics
        used_gb = bytes_to_gb(self.disc_bytes_written)
        total_gb = bytes_to_gb(self.capacity)
        disc_pct = (self.disc_bytes_written / self.capacity * 100) if self.capacity else 0
        overall_pct = (self.cumulative_file_index / self.total_files * 100) if self.total_files else 100

        # Build each of the 3 lines
        line1 = f"Disc {self.disc_number:02d} ({used_gb:.2f} GB / {total_gb:.1f} GB — {disc_pct:5.1f}%)"
        line2 = f"  Folder: {self.current_folder}/"
        line3 = f"    [{self.cumulative_file_index:5d}/{self.total_files}]  {overall_pct:5.1f}% overall — {filename}"

        # Hard-truncate every line to prevent soft-wraps from corrupting the row count
        lines = [
            line1[:term_width - 1],
            line2[:term_width - 1],
            line3[:term_width - 1]
        ]

        if not self.has_drawn:
            # First frame: print an empty separating line, then the 3 lines cleanly
            sys.stdout.write(f"\n\r\033[K{lines[0]}\n")
            sys.stdout.write(f"\r\033[K{lines[1]}\n")
            sys.stdout.write(f"\r\033[K{lines[2]}")
            self.has_drawn = True
        else:
            # Subsequent frames: jump up 2 lines, redraw, and leave cursor at line 3
            sys.stdout.write("\033[2A")                 # Move up 2 lines
            sys.stdout.write(f"\r\033[K{lines[0]}\n")   # Clear & overwrite Line 1
            sys.stdout.write(f"\r\033[K{lines[1]}\n")   # Clear & overwrite Line 2
            sys.stdout.write(f"\r\033[K{lines[2]}")     # Clear & overwrite Line 3

        sys.stdout.flush()

    def finish(self):
        """Move cursor below the 3-line block once the disc finishes."""
        print()

    def get_cumulative_index(self):
        """Pass the overall file counter to the next disc's tracker."""
        return self.cumulative_file_index

def find_top_level_folders(source_folder):
    """Find all top-level directories in the source root, ignoring symlinks."""
    folders = [
        path
        for path in source_folder.iterdir()
        if path.is_dir() and not path.is_symlink()
    ]
    return sorted(folders, key=sort_key_case_insensitive)

def find_files(top_level_folder):
    """Recursively collect and sort all regular files inside a folder."""
    files = [
        path
        for path in top_level_folder.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    return sorted(
        files,
        key=lambda path: sort_key_case_insensitive(path.relative_to(top_level_folder)),
    )

def validate_files_fit_capacity(files, capacity):
    """Ensure no individual file is too large to fit on a single disc."""
    for file_path in files:
        file_size = file_path.stat().st_size
        if file_size > capacity:
            raise ValueError(
                "A single file is larger than the selected capacity:\n"
                f" File: {file_path}\n"
                f" Size: {file_size:,} bytes\n"
                f" Capacity: {capacity:,} bytes"
            )

def create_disc_plan(top_level_folders, capacity):
    """Greedily assign files to numbered discs while trying to keep folders together."""
    assignments = []
    disc_number = 1
    used_space = 0

    for top_level_folder in top_level_folders:
        files = find_files(top_level_folder)
        validate_files_fit_capacity(files, capacity)
        folder_size = sum(file_path.stat().st_size for file_path in files)

        # Try to keep the entire folder intact on the next disc if it won't fit here
        if used_space > 0 and used_space + folder_size > capacity:
            disc_number += 1
            used_space = 0

        # If a folder exceeds disc capacity on its own, roll over individual files as needed
        for file_path in files:
            file_size = file_path.stat().st_size
            if used_space > 0 and used_space + file_size > capacity:
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
    """Calculate multi-disc span counts for folders (e.g., 'Part 1 of 3')."""
    discs_by_folder = defaultdict(set)
    for item in assignments:
        folder_name = item["top_level_name"]
        disc = item["disc"]
        discs_by_folder[folder_name].add(disc)

    result = {}
    for folder_name, discs in discs_by_folder.items():
        sorted_discs = sorted(discs)
        total_parts = len(sorted_discs)
        for part_number, disc in enumerate(sorted_discs, start=1):
            result[(folder_name, disc)] = (part_number, total_parts)

    return result

def get_output_folder_name(folder_name, disc, folder_parts):
    """Format folder name, appending split suffixes only if it spans multiple discs."""
    part_number, total_parts = folder_parts[(folder_name, disc)]
    if total_parts == 1:
        return folder_name
    return f"{folder_name} ({part_number} out of {total_parts})"

def get_disc_folder_path(output_folder, disc_number):
    """Generate the standardized target folder name for a given disc index."""
    return output_folder / f"Disc {disc_number:02d}"

def print_disc_plan(assignments, capacity):
    """Print an overview of total discs required and storage distribution."""
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
    """Display the planned folder layout showing which folders land on which disc."""
    folder_parts = calculate_folder_parts(assignments)
    disc_folders = defaultdict(set)

    for item in assignments:
        folder_name = item["top_level_name"]
        disc = item["disc"]
        output_name = get_output_folder_name(folder_name, disc, folder_parts)
        disc_folders[disc].add(output_name)

    if not disc_folders:
        print("No output structure to display.")
        return

    print("\nOutput structure")
    print("----------------")
    for disc in sorted(disc_folders.keys()):
        print(f"Disc {disc:02d}/")
        for folder_name in sorted(disc_folders[disc]):
            print(f"  {folder_name}/")
        print(f"  {INTEGRITY_FOLDER_NAME}/")

def copy_files(assignments, output_folder, capacity):
    """Copy assigned files into their corresponding Disc output folders."""
    assignments_by_disc = defaultdict(list)
    for item in assignments:
        disc = item["disc"]
        assignments_by_disc[disc].append(item)

    folder_parts = calculate_folder_parts(assignments)
    total_files = len(assignments)

    print("\nCopying files to discs...")
    cumulative_file_index = 0

    for disc in sorted(assignments_by_disc.keys()):
        disc_assignments = assignments_by_disc[disc]

        progress = ProgressTracker(
            disc_number=disc,
            capacity=capacity,
            total_files=total_files,
            cumulative_start=cumulative_file_index,
        )

        for item in disc_assignments:
            folder_name = item["top_level_name"]
            source_top_level_folder = item["top_level_folder"]
            source_file = item["file"]
            file_size = item["size"]

            # Reconstruct original subfolder hierarchy inside the disc directory
            output_top_level_name = get_output_folder_name(
                folder_name, disc, folder_parts
            )
            disc_folder = get_disc_folder_path(output_folder, disc)
            output_top_level_folder = disc_folder / output_top_level_name
            relative_path = source_file.relative_to(source_top_level_folder)
            destination_file = output_top_level_folder / relative_path

            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)

            progress.update(
                file_size=file_size,
                folder_name=folder_name,
                filename=source_file.name,
            )

        progress.finish()
        cumulative_file_index = progress.get_cumulative_index()

def create_checksums_for_disc(disc_number, disc_folder, capacity):
    """Generate SHA-256 hashes for all files on a disc and save them to a verification file."""
    integrity_folder = disc_folder / INTEGRITY_FOLDER_NAME
    integrity_folder.mkdir(parents=True, exist_ok=True)

    # Exclude files inside the integrity folder so we don't hash the hash file itself
    files_to_checksum = [
        path
        for path in disc_folder.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and INTEGRITY_FOLDER_NAME not in path.parts
    ]
    files_to_checksum.sort(
        key=lambda path: sort_key_case_insensitive(path.relative_to(disc_folder))
    )

    checksum_file = integrity_folder / CHECKSUM_FILENAME

    disc_bytes = sum(f.stat().st_size for f in files_to_checksum)
    used_gb = bytes_to_gb(disc_bytes)
    total_gb = bytes_to_gb(capacity)
    print(f"\nDisc {disc_number:02d} ({used_gb:.1f} GB / {total_gb:.1f} GB)")
    print("  Creating checksums...")

    with checksum_file.open("w") as f:
        for idx, file_path in enumerate(files_to_checksum, start=1):
            file_checksum = sha256_file(file_path)
            relative_path = file_path.relative_to(disc_folder)
            f.write(f"{file_checksum}  {relative_path}\n")

            percentage = idx / len(files_to_checksum) * 100 if files_to_checksum else 100
            progress_line = (
                f"    [{idx:5d}/{len(files_to_checksum)}]  "
                f"{percentage:5.1f}% — {file_path.name}"
            )
            print(f"\r{progress_line}\033[K", end="", flush=True)

    print()

def create_verification_instructions_for_disc(disc_number, disc_folder):
    """Write user instructions for verifying disc contents using the checksum file."""
    integrity_folder = disc_folder / INTEGRITY_FOLDER_NAME
    integrity_folder.mkdir(parents=True, exist_ok=True)
    instructions_file = integrity_folder / INSTRUCTIONS_FILENAME
    instructions_file.write_text(INSTRUCTIONS_TEXT)

def create_integrity_data(output_folder, disc_count, capacity):
    """Generate checksums and verification README files across all created disc folders."""
    for disc_number in range(1, disc_count + 1):
        disc_folder = get_disc_folder_path(output_folder, disc_number)
        if disc_folder.exists():
            create_checksums_for_disc(disc_number, disc_folder, capacity)
            create_verification_instructions_for_disc(disc_number, disc_folder)

def main():
    """Parse command line arguments and execute the disc preparation workflow."""
    parser = argparse.ArgumentParser(
        description="Organize files into disc-sized folders with integrity verification."
    )
    parser.add_argument(
        "source_folder",
        type=Path,
        help="Source folder containing top-level folders to organize.",
    )
    parser.add_argument(
        "output_folder",
        type=Path,
        help="Output folder where Disc XX folders will be created.",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=DEFAULT_CAPACITY,
        help=f"Maximum bytes per disc (default: {DEFAULT_CAPACITY:,}).",
    )
    parser.add_argument(
        "--skip-checksums",
        action="store_true",
        help="Skip checksum creation; only copy files.",
    )
    args = parser.parse_args()

    if not args.source_folder.is_dir():
        print(f"Error: Source folder not found: {args.source_folder}", file=sys.stderr)
        sys.exit(1)

    top_level_folders = find_top_level_folders(args.source_folder)
    if not top_level_folders:
        print("Error: No top-level folders found in source folder.", file=sys.stderr)
        sys.exit(1)

    assignments = create_disc_plan(top_level_folders, args.capacity)
    if not assignments:
        print("Error: No files found to organize.", file=sys.stderr)
        sys.exit(1)

    disc_count = max(item["disc"] for item in assignments)

    # Always show plan and target structure first
    print_disc_plan(assignments, args.capacity)
    print_output_structure(assignments)

    # Prompt user before writing anything to disk
    try:
        response = input("\nDo you want to proceed with copying files? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    if response not in ("y", "yes"):
        print("Operation cancelled. No files were copied.")
        sys.exit(0)

    args.output_folder.mkdir(parents=True, exist_ok=True)
    copy_files(assignments, args.output_folder, args.capacity)

    if not args.skip_checksums:
        create_integrity_data(args.output_folder, disc_count, args.capacity)

    print("\nDone!")

if __name__ == "__main__":
    main()

