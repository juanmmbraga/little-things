#!/usr/bin/env python3
"""
IMAGE RENAMER TOOL
==================

WHAT THIS DOES:
This script automatically renames image files based on WHEN THE PHOTO WAS TAKEN.
It reads the date from the image's metadata (called "EXIF data") and renames the 
file to a standardized format: YYYYMMDD_HHMMSS (e.g., 20260914_143022.jpg)

This makes it easy to organize photos by date and avoids duplicate filenames.

USAGE:
  python rename.py /path/to/folder              # Rename all images in folder
  python rename.py /path/to/folder --dry-run    # Preview changes (don't actually rename)

SUPPORTED IMAGE TYPES: JPG, PNG, GIF, BMP, AVIF, HEIC, WebP
"""

import os
import re
import sys
from pathlib import Path
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS

# Image file types this script can process (case-insensitive)
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.avif', '.heic', '.webp'}

# Pattern to check if a file is already correctly named (starts with YYYYMMDD_HHMMSS)
CORRECT_NAME_PATTERN = re.compile(r'^\d{8}_\d{6}')


def get_exif_date(image_path):
    """
    Read when the photo was actually taken from its metadata.
    
    Returns: A datetime object, or None if we can't find the date.
    
    Why this matters: Cameras embed the photo's taken-time in EXIF data.
    This is more reliable than file modification time.
    """
    try:
        image = Image.open(image_path)
        exif_data = image._getexif()
        
        # If no EXIF data exists, return None
        if not exif_data:
            return None
        
        # Look for "DateTime Original" (36867) first (when photo was taken)
        # Fall back to "DateTime" (36868) if not found
        date_taken = exif_data.get(36867) or exif_data.get(36868)
        
        if date_taken:
            # Convert EXIF format "YYYY:MM:DD HH:MM:SS" to a Python datetime object
            return datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
    
    except Exception:
        # If anything goes wrong (corrupt file, no EXIF data, etc.), just fail silently
        pass
    
    return None


def is_correctly_named(filename):
    """
    Check if a file is already in the correct format (YYYYMMDD_HHMMSS_*).
    
    If already correct, we don't need to rename it.
    """
    name_without_ext = os.path.splitext(filename)[0]
    return bool(CORRECT_NAME_PATTERN.match(name_without_ext))


def rename_file(file_path, new_name, dry_run=False):
    """
    Rename a file to the new name.
    
    Args:
      file_path: The current full path to the file
      new_name: The new filename (without the folder path)
      dry_run: If True, don't actually rename (just pretend)
    
    Returns: True if successful, False if a file with that name already exists
    """
    parent_dir = os.path.dirname(file_path)
    new_path = os.path.join(parent_dir, new_name)
    
    # Check if new filename already exists (avoid overwriting)
    if os.path.exists(new_path):
        return False
    
    # Only actually rename if NOT in dry-run mode
    if not dry_run:
        os.rename(file_path, new_path)
    
    return True


def process_folder(folder_path, dry_run=False):
    """
    Main function: Find all images in a folder (and subfolders) and rename them.
    
    Categorizes results into:
      - RENAMED: Successfully renamed files
      - SKIPPED: Files that couldn't be renamed (no date found, conflicts, etc.)
      - OTHER: Unsupported files or files already correct
    """
    folder_path = Path(folder_path)
    
    # Verify the folder exists
    if not folder_path.exists() or not folder_path.is_dir():
        print(f"[Error: folder not found] {folder_path}")
        return
    
    # Organize results by category for clear reporting
    renamed = []
    skipped = []
    other = []
    
    # Walk through all folders and subfolders
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            file_ext = os.path.splitext(filename)[1].lower()
            
            # STEP 1: Check if this is a supported image type
            if file_ext not in SUPPORTED_EXTENSIONS:
                other.append(f"[Skipped: unsupported file] {file_path}")
                continue
            
            # STEP 2: Check if already correctly named
            if is_correctly_named(filename):
                other.append(f"[Already correct] {file_path}")
                continue
            
            # STEP 3: Try to read the photo's taken-date from EXIF metadata
            exif_date = get_exif_date(file_path)
            
            if not exif_date:
                skipped.append(f"[Skipped: no EXIF date] {file_path}")
                continue
            
            # STEP 4: Create new filename based on the date
            new_name = exif_date.strftime("%Y%m%d_%H%M%S") + file_ext
            
            # STEP 5: Try to rename the file
            try:
                if rename_file(file_path, new_name, dry_run):
                    renamed.append(f"[Renamed] {file_path} → {new_name}")
                else:
                    skipped.append(f"[Skipped: file conflict] {file_path}")
            except Exception as e:
                other.append(f"[Error: {str(e)}] {file_path}")
    
    # Print results organized by category
    if renamed:
        print("RENAMED:\n")
        for line in renamed:
            print(line)
        print()
    
    if skipped:
        print("SKIPPED:\n")
        for line in skipped:
            print(line)
        print()
    
    if other:
        print("OTHER:\n")
        for line in other:
            print(line)
        print()


def main():
    """Parse command-line arguments and start the renaming process."""
    dry_run = False
    folder_path = None
    
    # Read arguments from the command line
    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        else:
            folder_path = arg
    
    # Show help if no folder was provided
    if not folder_path:
        print("Usage: python rename.py <folder_path> [--dry-run]")
        print("\nExamples:")
        print("  python rename.py ~/Pictures")
        print("  python rename.py ~/Pictures --dry-run")
        sys.exit(1)
    
    if dry_run:
        print("[DRY RUN MODE - no files will be renamed]\n")
    
    process_folder(folder_path, dry_run)


if __name__ == "__main__":
    main()

