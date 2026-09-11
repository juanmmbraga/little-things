#!/usr/bin/env python3
"""
convert_to_avif.py

Recursively convert image files to AVIF, preserving metadata and file timestamps.
Usage example:
  python3 convert_to_avif.py . --quality 70 --speed 5 --yuv 444 --delete-originals
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
DEFAULT_QUALITY = 70
DEFAULT_AVIF_SPEED = 5
DEFAULT_YUV_FORMAT = "444"
BYTES_IN_MB = 1024 * 1024

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".jxl"
}

try:
    from tqdm import tqdm
except ImportError:
    # Lightweight shim to prevent crashes if tqdm is not installed
    class tqdm:
        def __init__(self, iterable, *args, **kwargs):
            self.iterable = iterable
        def __iter__(self):
            return iter(self.iterable)
        def __len__(self):
            return len(self.iterable)
        @staticmethod
        def write(text):
            print(text)

def check_command(cmd):
    return shutil.which(cmd) is not None

def run(cmd):
    completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.returncode, completed.stdout.decode(errors='ignore'), completed.stderr.decode(errors='ignore')

def human_size(n):
    if not n:
        return "0.00 MB"
    return f"{n / BYTES_IN_MB:.2f} MB"

def human_time(seconds):
    if seconds < 60:
        return f"{seconds:.2f} seconds"
    minutes, sec = divmod(seconds, 60)
    return f"{int(minutes)}m {sec:.2f}s"

def encode_avif(src: Path, dst: Path, quality: int, speed: int, yuv: str, threads: int):
    if not check_command("avifenc"):
        return False, "", "avifenc not found. Please install libavif-bin."
    cmd = [
        "avifenc", 
        "-j", str(threads), 
        "-s", str(speed), 
        "-q", str(quality), 
        "--yuv", yuv, 
        "-o", str(dst), 
        str(src)
    ]
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
        if p.suffix.lower() in IMAGE_EXTENSIONS:
            yield p

def process_file(src: Path, quality: int, speed: int, yuv: str, threads: int, dry_run: bool, size_check_enabled: bool, size_threshold_mb: float):
    original_size = src.stat().st_size
    dst = src.with_suffix(".avif")
    
    if dst.exists() and dst.stat().st_mtime > src.stat().st_mtime and not dry_run:
        return {"path": src, "status": "skipped", "msg": "Destination exists and is newer", "before": original_size, "after": dst.stat().st_size, "saved": 0}

    if dry_run:
        return {"path": src, "status": "dryrun", "msg": f"Would convert to {dst.name}", "before": original_size, "after": 0, "saved": 0}

    tmpdst = make_tmpdst(dst)
    try:
        ok, out, err = encode_avif(src, tmpdst, quality, speed, yuv, threads)
        if not ok:
            if tmpdst.exists():
                try: tmpdst.unlink()
                except Exception: pass
            return {"path": src, "status": "error", "msg": f"Encoding failed: {err.strip() or out.strip() or 'unknown error'}", "before": original_size, "after": 0, "saved": 0}

        new_size = tmpdst.stat().st_size
        saved = original_size - new_size
        
        if size_check_enabled and abs(saved) <= size_threshold_mb * BYTES_IN_MB:
            if tmpdst.exists():
                try: tmpdst.unlink()
                except Exception: pass
            return {"path": src, "status": "skipped_size", "msg": "Below or equal to threshold", "before": original_size, "after": new_size, "saved": saved}

        copy_metadata(src, tmpdst)
        try:
            preserve_timestamp(src, tmpdst)
        except Exception:
            pass

        tmpdst.replace(dst)
        return {"path": src, "status": "done", "msg": "metadata preserved", "before": original_size, "after": new_size, "saved": saved}

    except Exception as e:
        if tmpdst.exists():
            try: tmpdst.unlink()
            except Exception: pass
        return {"path": src, "status": "error", "msg": str(e), "before": original_size, "after": 0, "saved": 0}

def main():
    parser = argparse.ArgumentParser(description="Convert images to AVIF recursively.")
    parser.add_argument("root", nargs="?", default=".", help="Root folder to process")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY, help="Quality 0-100")
    parser.add_argument("--speed", type=int, default=DEFAULT_AVIF_SPEED, help="avifenc speed/effort 0-10")
    parser.add_argument("--yuv", default=DEFAULT_YUV_FORMAT, choices=("420", "422", "444", "400"), help="Chroma subsampling")
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

    if not check_command("avifenc"):
        print("avifenc not found in PATH. Please install libavif-bin.", file=sys.stderr)
        sys.exit(2)

    files = list(find_images(root, "avif"))
    if not files:
        print("No images found under", root)
        return

    print("\n--- Runtime Settings ---")
    print(f"Root Folder      : {root}")
    print(f"Target Format    : AVIF")
    print(f"Quality          : {args.quality}")
    print(f"AVIF Speed       : {args.speed}")
    print(f"YUV Format       : {args.yuv}")
    print(f"Max Parallel     : {args.max_workers if args.max_workers else 'Default (CPU count)'}")
    print(f"Dry Run          : {args.dry_run}")
    print(f"Delete Originals : {args.delete_originals}")
    print(f"Size Check       : {args.size_check} (Threshold: {args.size_threshold} MB)")
    print("-----------------------\n")

    totals_before = 0
    totals_after = 0
    results = {"done": 0, "skipped": 0, "skipped_size": 0, "error": 0, "dryrun": 0}
    
    unsupported_list = []
    error_list = []
    
    print("--- Starting Image Conversion (AVIF) ---")

    tag_mapping = {
        "done": "Done",
        "skipped": "Skipped",
        "skipped_size": "Skipped (size-filter)",
        "error": "Error",
        "dryrun": "Dry-run"
    }

    batch_start_time = time.time()

    with ProcessPoolExecutor(max_workers=args.max_workers) as executor:
        futures = [
            executor.submit(
                process_file, p, args.quality, args.speed, args.yuv, 
                args.threads, args.dry_run, args.size_check, args.size_threshold
            )
            for p in files
        ]
        
        for future in tqdm(as_completed(futures), total=len(futures)):
            res = future.result()
            status = res["status"]
            results[status] = results.get(status, 0) + 1
            tag = tag_mapping.get(status, status.upper())

            before_size = res["before"]
            after_size = res["after"]
            saved_delta = res["saved"]

            if status == "skipped_size":
                diff_mb = saved_delta / BYTES_IN_MB
                pct_change = (saved_delta / before_size * 100) if before_size else 0.0
            else:
                diff_mb = (before_size - after_size) / BYTES_IN_MB if after_size > 0 else 0.0
                pct_change = ((before_size - after_size) / before_size * 100) if before_size > 0 and after_size > 0 else 0.0

            disp_after = after_size if after_size > 0 else (before_size if status in ("skipped", "skipped_size") else 0)
            
            # Use tqdm.write instead of print to keep the progress bar at the bottom
            tqdm.write(f"[{tag}] {res['path']} ({human_size(before_size)} → {human_size(disp_after)}) [Diff: {diff_mb:+.2f} MB ({pct_change:+.2f}%)] [{res['msg']}]")

            if status == "done":
                totals_before += before_size
                totals_after += after_size
                if args.delete_originals and not args.dry_run:
                    try:
                        res["path"].unlink()
                        tqdm.write(f"          -> Deleted original file.")
                    except Exception as e:
                        tqdm.write(f"          [WARN] Could not delete original: {e}")
            elif status in ("skipped", "skipped_size"):
                totals_before += before_size
                totals_after += before_size
            elif status == "error":
                error_list.append((res["path"], res["msg"]))

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
            print(f"Skipped (Size Threshold): {results.get('skipped_size', 0)}")
        print(f"Average Image Rate      : {avg_processing_rate:.2f} wall-clock seconds/image")

    if error_list:
        print(f"Processing Errors       : {len(error_list)}")
        
    if totals_before > 0:
        print("-" * 50)
        print(f"Total Space Processed   : {human_size(totals_before)}")
        print(f"Total Space Saved       : {human_size(total_saved)} ({total_pct:.1f}% reduction)")
        print("="*50)

    if error_list:
        print("\n=== Processing Errors Details ===")
        for path, err_msg in error_list:
            print(f"  - {path}: {err_msg}")

if __name__ == "__main__":
    main()

