#!/usr/bin/env python3
"""
Rename image files based on EXIF "Date Taken" metadata.
Supports: .jpg, .jpeg, .png, .gif, .bmp, .avif, .heic, .webp
Recursively searches all subdirectories.
"""

import os
import sys
import argparse
from pathlib import Path
from PIL import Image
from PIL.ExifTags import TAGS
from datetime import datetime
from collections import defaultdict

# Supported image extensions
SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.avif', '.heic', '.webp'}

# Screenshot prefixes (case-insensitive)
SCREENSHOT_PREFIXES = ('screenshot_', 'screen shot_', 'screenshoot_', 'screenshot-', 'screen shot-')

# EXIF date tags
EXIF_DATETIME_ORIGINAL = 36867  # DateTimeOriginal
EXIF_DATETIME = 36868           # DateTime


class Statistics:
    """Track renaming operation statistics."""
    def __init__(self):
        self.total_files = 0
        self.renamed = 0
        self.already_correct = 0
        self.skipped = 0
        self.preserved_screenshots = 0
        self.errors = 0

    def display(self):
        """Print a formatted summary of statistics."""
        print("\n" + "="*60)
        print("SUMMARY STATISTICS")
        print("="*60)
        print(f"Total files processed:      {self.total_files}")
        print(f"Renamed:                    {self.renamed}")
        print(f"Already correct:            {self.already_correct}")
        print(f"Skipped:                    {self.skipped}")
        print(f"Preserved (screenshots):    {self.preserved_screenshots}")
        print(f"Errors:                     {self.errors}")
        print("="*60 + "\n")
    
    def display_brief(self, show_skipped=False):
        """Print a brief summary before renaming."""
        print("\n" + "="*60)
        print("STATUS")
        print("="*60)
        print(f"Already correct:            {self.already_correct}")
        if show_skipped:
            print(f"Skipped:                    {self.skipped}")
        print("="*60 + "\n")


def get_exif_date(image_path):
    """
    Extract the date taken from EXIF metadata.
    Returns: datetime object or None
    """
    try:
        image = Image.open(image_path)
        exif_data = image._getexif()
        
        if exif_data is None:
            return None
        
        # Try DateTime Original first, then DateTime
        for tag_id in [EXIF_DATETIME_ORIGINAL, EXIF_DATETIME]:
            if tag_id in exif_data:
                date_string = exif_data[tag_id]
                try:
                    return datetime.strptime(date_string, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    continue
        
        return None
    except Exception:
        return None


def is_correctly_named(filename):
    """Check if filename matches the correct pattern: YYYYMMDD_HHMMSS"""
    name_without_ext = Path(filename).stem
    return bool(__import__('re').match(r'^\d{8}_\d{6}$', name_without_ext))


def is_screenshot(filename):
    """Check if filename starts with screenshot prefixes."""
    filename_lower = filename.lower()
    return any(filename_lower.startswith(prefix) for prefix in SCREENSHOT_PREFIXES)


def get_unique_filename(directory, base_name, extension):
    """
    Generate a unique filename by adding _1, _2, etc. suffix if needed.
    """
    target_path = Path(directory) / f"{base_name}{extension}"
    
    if not target_path.exists():
        return target_path.name
    
    counter = 1
    while True:
        new_name = f"{base_name}_{counter}{extension}"
        new_path = Path(directory) / new_name
        if not new_path.exists():
            return new_name
        counter += 1


def rename_file(old_path, new_name, dry_run=False):
    """
    Rename a file. If dry_run is True, just show what would happen.
    Returns: (success: bool, message: str)
    """
    new_path = old_path.parent / new_name
    
    if dry_run:
        return True, f"{old_path.name} → {new_name}"
    
    try:
        old_path.rename(new_path)
        return True, f"{old_path.name} → {new_name}"
    except Exception as e:
        return False, f"Error renaming {old_path.name}: {e}"


def process_folder(folder_path, dry_run=False, show_skipped=False, force=False):
    """
    Recursively process all image files in the folder and subfolders.
    If force=True, skip confirmation prompt.
    """
    folder = Path(folder_path)
    if not folder.is_dir():
        print(f"Error: '{folder_path}' is not a directory.")
        return
    
    stats = Statistics()
    to_rename = []      # List of (old_path, new_name, rel_path) tuples
    skipped_info = []   # List of (relative_path, reason) tuples
    correct_info = []   # List of relative_path tuples
    
    # Scan all files recursively first
    print(f"Scanning folder recursively: {folder}")
    print("-" * 60)
    
    for file_path in sorted(folder.rglob('*')):
        if not file_path.is_file():
            continue
        
        stats.total_files += 1
        extension = file_path.suffix.lower()
        rel_path = file_path.relative_to(folder)
        
        # Skip unsupported extensions
        if extension not in SUPPORTED_EXTENSIONS:
            stats.skipped += 1
            skipped_info.append((rel_path, "unsupported format"))
            continue
        
        # Preserve screenshots
        if is_screenshot(file_path.name):
            stats.preserved_screenshots += 1
            skipped_info.append((rel_path, "screenshot (preserved)"))
            continue
        
        # Check if already correctly named
        if is_correctly_named(file_path.name):
            stats.already_correct += 1
            correct_info.append(rel_path)
            continue
        
        # Try to get EXIF date
        exif_date = get_exif_date(file_path)
        if exif_date is None:
            stats.skipped += 1
            skipped_info.append((rel_path, "no EXIF date found"))
            continue
        
        # Generate new filename
        date_string = exif_date.strftime('%Y%m%d_%H%M%S')
        new_name = get_unique_filename(file_path.parent, date_string, extension)
        
        if new_name == file_path.name:
            # Already has correct name (shouldn't happen, but just in case)
            stats.already_correct += 1
            correct_info.append(rel_path)
        else:
            # Will be renamed
            stats.renamed += 1
            to_rename.append((file_path, new_name, rel_path))
    
    # Display plan
    print("\n" + "="*60)
    print("PLAN SUMMARY")
    print("="*60)
    
    # Show files to rename grouped by directory
    if to_rename:
        print(f"\nFILES TO BE RENAMED ({len(to_rename)}):\n")
        
        # Group by directory
        by_directory = defaultdict(list)
        for old_path, new_name, rel_path in to_rename:
            directory = rel_path.parent
            by_directory[directory].append((rel_path.name, new_name))
        
        # Print tree structure
        sorted_dirs = sorted(by_directory.keys())
        for i, directory in enumerate(sorted_dirs):
            is_last_dir = (i == len(sorted_dirs) - 1)
            dir_prefix = "└── " if is_last_dir else "├── "
            print(f"{dir_prefix}{directory}/")
            
            files = by_directory[directory]
            for j, (old_name, new_name) in enumerate(files):
                is_last_file = (j == len(files) - 1)
                # Use appropriate spacing based on whether dir is last
                if is_last_dir:
                    file_prefix = "    └─ " if is_last_file else "    ├─ "
                else:
                    file_prefix = "    └─ " if is_last_file else "    ├─ "
                print(f"{file_prefix}{old_name} → {new_name}")
    else:
        print(f"\nFILES TO BE RENAMED: 0")
    
    # Show skipped files only if flag is set
    if show_skipped and skipped_info:
        print(f"\nSKIPPED FILES ({len(skipped_info)}):")
        print("-" * 60)
        for rel_path, reason in skipped_info:
            print(f"  {str(rel_path):<50} ({reason})")
    
    print("\n" + "="*60)
    
    # If no operations, we're done
    if not to_rename:
        print("\nNo files to rename.")
        stats.display()
        return
    
    # Show confirmation prompt unless --force or --dry-run
    if dry_run:
        print("\n[DRY-RUN MODE] No files were actually renamed.")
        stats.display()
        return
    
    if not force:
        print(f"\nReady to rename {len(to_rename)} file(s).")
        response = input("Proceed with renaming? (yes/no): ").strip().lower()
        
        if response not in ('yes', 'y'):
            print("Cancelled. No files were renamed.")
            stats.display()
            return
    
    # Show brief statistics after confirmation
    stats.display_brief(show_skipped=show_skipped)
    
    # Perform the actual renames
    print("Renaming files...")
    print("-" * 60)
    for old_path, new_name, rel_path in to_rename:
        success, message = rename_file(old_path, new_name, dry_run=False)
        if success:
            print(f"[OK] {rel_path} → {new_name}")
        else:
            print(f"[ERROR] {message}")
            stats.errors += 1
    
    # Display final statistics
    stats.display()


def main():
    parser = argparse.ArgumentParser(
        description="Rename image files based on EXIF 'Date Taken' metadata (searches recursively)."
    )
    parser.add_argument(
        'folder',
        nargs='?',
        default='.',
        help='Folder containing images (default: current directory)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview changes without renaming'
    )
    parser.add_argument(
        '--show-skipped',
        action='store_true',
        help='Show files that were skipped and their reasons'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompt and rename immediately'
    )
    
    args = parser.parse_args()
    process_folder(args.folder, dry_run=args.dry_run, show_skipped=args.show_skipped, force=args.force)


if __name__ == '__main__':
    main()

