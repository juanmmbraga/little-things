#!/usr/bin/env python3
"""
This script recursively renames image files in a specified folder and its subfolders based on the date and time they were taken (extracted from EXIF metadata). 
Files are renamed to a standardized format: YYYYMMDD_HHMMSS.ext (e.g., 20231025_143005.jpg).

Requirement: pip install Pillow

Usage:  
    python rename.py <folder_path>             # To rename files  
    python rename.py <folder_path> --dry-run   # To preview changes without renaming
"""
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS

# Supported image extensions (case-insensitive)
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.avif', '.heic', '.webp'}

# Pattern for correctly named files: YYYYMMDD_HHMMSS
CORRECT_NAME_PATTERN = re.compile(r'^\d{8}_\d{6}')


def get_exif_date(image_path):
    """Extract date from EXIF data. Returns datetime object or None."""
    try:
        image = Image.open(image_path)
        exif_data = image._getexif()
        
        if not exif_data:
            return None
        
        # Look for DateTime Original (36867) first, then DateTime (36868)
        date_taken = exif_data.get(36867) or exif_data.get(36868)
        
        if date_taken:
            # Parse EXIF date format: "YYYY:MM:DD HH:MM:SS"
            return datetime.strptime(date_taken, "%Y:%m:%d %H:%M:%S")
    
    except Exception:
        pass
    
    return None


def is_correctly_named(filename):
    """Check if filename starts with YYYYMMDD_HHMMSS pattern."""
    name_without_ext = os.path.splitext(filename)[0]
    return bool(CORRECT_NAME_PATTERN.match(name_without_ext))


def rename_file(file_path, new_name, dry_run=False):
    """Rename file. Returns True if successful, False if conflict."""
    parent_dir = os.path.dirname(file_path)
    new_path = os.path.join(parent_dir, new_name)
    
    if os.path.exists(new_path):
        return False  # File conflict
    
    if not dry_run:
        os.rename(file_path, new_path)
    
    return True


def process_folder(folder_path, dry_run=False):
    """Recursively process all image files in folder and subfolders."""
    folder_path = Path(folder_path)
    
    if not folder_path.exists() or not folder_path.is_dir():
        print(f"[Error: folder not found] {folder_path}")
        return
    
    # Collect results by category
    renamed = []
    skipped = []
    other = []
    
    for root, dirs, files in os.walk(folder_path):
        for filename in files:
            file_path = os.path.join(root, filename)
            file_ext = os.path.splitext(filename)[1].lower()
            
            # Check if supported image file
            if file_ext not in SUPPORTED_EXTENSIONS:
                other.append(f"[Skipped: unsupported file] {file_path}")
                continue
            
            # Check if already correctly named
            if is_correctly_named(filename):
                other.append(f"[Already correct] {file_path}")
                continue
            
            # Try to get EXIF date
            exif_date = get_exif_date(file_path)
            
            if not exif_date:
                skipped.append(f"[Skipped: no EXIF date] {file_path}")
                continue
            
            # Generate new filename
            new_name = exif_date.strftime("%Y%m%d_%H%M%S") + file_ext
            
            # Try to rename
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
    # Parse command-line arguments
    dry_run = False
    folder_path = None
    
    for arg in sys.argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        else:
            folder_path = arg
    
    if not folder_path:
        print("Usage: python rename.py <folder_path> [--dry-run]")
        sys.exit(1)
    
    if dry_run:
        print("[DRY RUN MODE - no files will be renamed]\n")
    
    process_folder(folder_path, dry_run)


if __name__ == "__main__":
    main()

