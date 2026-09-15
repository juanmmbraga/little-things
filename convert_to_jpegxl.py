#!/usr/bin/env python3
"""
convert_to_jxl.py

Recursively convert image files (JPEG/PNG/etc.) to JXL, preserving metadata and file timestamps.
Usage example:
  python3 convert_to_jxl.py . --threads 4 --delete-originals
"""
from pathlib import Path
import argparse
import shutil
import subprocess
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

# --- CONSTANTS & CONFIGURATION ---
DEFAULT_SIZE_THRESHOLD_MB = 0.3
BYTES_IN_KB = 1024.0
BYTES_IN_MB = 1024 * 1024

IMAGE_MIME_EXTENSIONS = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".avif": "image/avif",
    ".jxl": "image/jxl",
}

try:
    from tqdm import tqdm
except Exception:
    tqdm = lambda x, **kwargs: x

try:
    import magic
    _have_magic = True
except Exception:
    magic = None
    _have_magic = False

def check_command(cmd):
    return shutil.which(cmd) is not None

def run(cmd):
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.returncode, completed.stdout.decode(errors='ignore'), completed.stderr.decode(errors='ignore')

def human_size(n):
    if n is None:
        return "0.0B"
    n = float(n)
    for unit in ['B','KB','MB','GB','TB']:
        if n < BYTES_IN_KB:
            if n < 10.0:
                return f"{n:3.2f}{unit}"
            return f"{n:3.1f}{unit}"
        n /= BYTES_IN_KB
    return f"{n:.1f}PB"

def human_time(seconds):
    if seconds < 60:
        return f"{seconds:.2f} seconds"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {sec:.2f}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{int(hours)}h {int(minutes)}m {sec:.2f}s"
    days, hours = divmod(hours, 24)
    return f"{int(days)}d {int(hours)}h {int(minutes)}m {sec:.2f}s"

def detect_mime(path: Path):
    try:
        if _have_magic:
            m = magic.from_file(str(path), mime=True)
            if m:
                return m
    except Exception:
        pass
    ext = path.suffix.lower()
    return IMAGE_MIME_EXTENSIONS.get(ext, None)

def encode_jxl(src: Path, dst: Path, threads: int):
    if not check_command("cjxl"):
        return False, "", "cjxl not found. Please install libjxl-tools."
    cmd = ["cjxl", "--num_threads", str(threads), str(src), str(dst)]
    rc, out, err = run(cmd)
    return rc == 0, out, err

def copy_metadata(src: Path, dst: Path):
    if not check_command("exiftool"):
        return False, "exiftool not found"
    try:
        cmd = ["exiftool", "-overwrite_original", "-TagsFromFile", str(src), "-all:all", str(dst)]
        rc, out, err = run(cmd)
        if rc != 0:
            return False, f"exiftool failed (RC={rc}): {out + err}"
        return True, out + err
    except FileNotFoundError:
        return False, "exiftool command not found."

def preserve_timestamp(src: Path, dst: Path):
    st = src.stat()
    os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))

def make_tmpdst(dst: Path):
    parent = dst.parent
    base = dst.name
    pid = os.getpid()
    i = 0
    while True:
        suffix = f".tmp{pid}" if i == 0 else f".tmp{pid}.{i}"
        candidate = parent / (base + suffix)
        if not candidate.exists():
            return candidate
        i += 1

def find_images(root: Path, target_format: str):
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() == f".{target_format.lower()}":
            continue
        m = detect_mime(p)
        if m and m.startswith("image/"):
            yield p, m
        else:
            yield p, None

def process_file(src: Path, mime: str, threads: int, dry_run: bool, size_check_enabled: bool, size_threshold_mb: float):
    original_size = src.stat().st_size
    dst = src.with_suffix(".jxl")
    
    if mime is None:
        return src, "unsupported", "Not an image or unknown mime", original_size, 0, 0

    if mime.lower() in ("image/svg+xml", "image/x-icon", "image/vnd.microsoft.icon"):
        return src, "unsupported", f"Unsupported image subtype: {mime}", original_size, 0, 0

    if dst.exists() and dst.stat().st_mtime > src.stat().st_mtime and not dry_run:
        return src, "skipped", "Destination exists and is newer", original_size, dst.stat().st_size, 0

    if dry_run:
        return src, "dryrun", f"Would convert to {dst.name}", original_size, 0, 0

    tmpdst = make_tmpdst(dst)
    try:
        ok, out, err = encode_jxl(src, tmpdst, threads)
        
        if not ok:
            if tmpdst.exists():
                try: tmpdst.unlink()
                except Exception: pass
            return src, "error", f"Encoding failed: {err.strip() or out.strip() or 'unknown error'}", original_size, 0, 0

        new_size = tmpdst.stat().st_size
        saved = original_size - new_size
        
        if size_check_enabled:
            size_difference = abs(saved)
            if size_difference <= size_threshold_mb * BYTES_IN_MB:
                if tmpdst.exists():
                    try: tmpdst.unlink()
                    except Exception: pass
                return src, "skipped_size", f"Below or equal to threshold", original_size, new_size, saved

        meta_ok, _ = copy_metadata(src, tmpdst)
        meta_status = "metadata preserved" if meta_ok else "metadata failed"

        try:
            preserve_timestamp(src, tmpdst)
        except Exception:
            pass

        tmpdst.replace(dst)
        return src, "done", meta_status, original_size, new_size, saved

    except Exception as e:
        if tmpdst.exists():
            try: tmpdst.unlink()
            except Exception: pass
        return src, "error", str(e), original_size, 0, 0

def main():
    parser = argparse.ArgumentParser(description="Convert images to JXL recursively.")
    parser.add_argument("root", nargs="?", default=".", help="Root folder to process")
    parser.add_argument("--threads", type=int, default=1, help="Internal threads per encoder instance")
    parser.add_argument("--max-workers", type=int, default=None, help="Max parallel image conversions")
    parser.add_argument("--dry-run", action="store_true", help="Dry run")
    parser.add_argument("--delete-originals", action="store_true", help="Delete originals")
    parser.add_argument("--size-check", action="store_true", help="Skip if size difference is below or equal to threshold")
    parser.add_argument("--size-threshold", type=float, default=DEFAULT_SIZE_THRESHOLD_MB, help="Minimum size difference in MB")
    
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print("Root folder not found:", root, file=sys.stderr)
        sys.exit(2)

    if not check_command("cjxl"):
        print("cjxl not found in PATH. Please install libjxl-tools.", file=sys.stderr)
        sys.exit(2)

    files = list(find_images(root, "jxl"))
    if not files:
        print("No images found under", root)
        return

    print("\n--- Runtime Settings ---")
    print(f"Root Folder      : {root}")
    print(f"Target Format    : JXL")
    print(f"Threads/Worker   : {args.threads}")
    print(f"Max Parallel     : {args.max_workers if args.max_workers else 'Default (CPU count)'}")
    print(f"Dry Run          : {args.dry_run}")
    print(f"Delete Originals : {args.delete_originals}")
    print(f"Size Check       : {args.size_check} (Threshold: {args.size_threshold} MB)")
    print("-----------------------\n")

    totals_before = 0
    totals_after = 0
    results = {"done": 0, "skipped": 0, "skipped_size": 0, "unsupported": 0, "error": 0, "dryrun": 0}
    
    unsupported_list = []
    error_list = []
    
    print("--- Starting Image Conversion (JXL) ---")

    tag_mapping = {
        "done": "Done",
        "skipped": "Skipped",
        "skipped_size": "Skipped (size-filter)",
        "unsupported": "Unsupported",
        "error": "Error",
        "dryrun": "Dry-run"
    }

    batch_start_time = time.time()

    with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        futures = [
            executor.submit(
                process_file, p, mime, args.threads, args.dry_run, args.size_check, args.size_threshold
            )
            for p, mime in files
        ]
        
        for future in tqdm(as_completed(futures), total=len(futures)):
            p, status, msg, before_size, after_size, saved_delta = future.result()
            
            results[status] = results.get(status, 0) + 1
            tag = tag_mapping.get(status, status.upper())

            diff_mb = (before_size - after_size) / BYTES_IN_MB if after_size > 0 else 0.0
            pct_change = ((before_size - after_size) / before_size * 100) if before_size > 0 and after_size > 0 else 0.0

            if status == "done":
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [Diff: {diff_mb:+.2f} MB ({pct_change:+.2f}%)] [{msg}]")
                totals_before += before_size
                totals_after += after_size
                
                if args.delete_originals and not args.dry_run:
                    try:
                        p.unlink()
                        print(f"          -> Deleted original file.")
                    except Exception as e:
                        print(f"          [WARN] Could not delete original {p}: {e}")

            elif status == "skipped_size":
                totals_before += before_size
                totals_after += before_size  
                true_diff_mb = saved_delta / BYTES_IN_MB
                true_pct = (saved_delta / before_size * 100) if before_size else 0.0
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [Diff: {true_diff_mb:+.2f} MB ({true_pct:+.2f}%)] [{msg}]")
                
            elif status == "skipped":
                totals_before += before_size
                totals_after += before_size
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [Diff: 0.00 MB (0.00%)] [{msg}]")
                
            elif status == "unsupported":
                unsupported_list.append((p, msg))
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [{msg}]")
                
            elif status == "error":
                error_list.append((p, msg))
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [{msg}]")
                
            else:
                print(f"[{tag}] {p} ({human_size(before_size)} → {human_size(after_size)}) [{msg}]")

    batch_end_time = time.time()
    total_elapsed_time = batch_end_time - batch_start_time
    total_saved = totals_before - totals_after
    total_pct = (total_saved / totals_before * 100) if totals_before else 0.0

    print("\n" + "="*50)
    print("                    CONVERSION SUMMARY")
    print("="*50)
    print(f"Total Elapsed Time      : {human_time(total_elapsed_time)}")
    
    total_files_encoded = results.get("done", 0) + results.get("skipped_size", 0)
    if total_files_encoded > 0:
        avg_processing_rate = total_elapsed_time / total_files_encoded
        print(f"Files Successfully Done : {results.get('done', 0)}")
        if args.size_check:
            print(f"Skipped (Size Threshold): {results.get('skipped_size', 0)} (Threshold: {args.size_threshold:.2f} MB)")
        print(f"Average Image Rate      : {avg_processing_rate:.2f} wall-clock seconds/image")

    if unsupported_list:
        print(f"Unsupported Files       : {len(unsupported_list)}")
    if error_list:
        print(f"Processing Errors       : {len(error_list)}")
        
    if totals_before > 0:
        print("-" * 50)
        print(f"Total Space Processed   : {human_size(totals_before)}")
        print(f"Total Space Saved       : {human_size(total_saved)} ({total_pct:.1f}% reduction)")
        print("="*50)

    if unsupported_list:
        print("\n=== Unsupported Files Details ===")
        for path, reason in unsupported_list:
            print(f"  - {path} ({reason})")

    if error_list:
        print("\n=== Processing Errors Details ===")
        for path, err_msg in error_list:
            print(f"  - {path}: {err_msg}")

if __name__ == "__main__":
    main()
