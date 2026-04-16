#!/usr/bin/env python3
"""
Generate identity mapping between GitHub logins and ServiceNow user IDs.

Reads github_users.csv and sys_user.csv from a target folder, performs
case-insensitive matching (exact then substring), and outputs:
  - identity_mapping.csv   (matched pairs: login, u_td_user_id, match_type)
  - identity_unmatched.csv (unmatched users from both sides)

Matching strategy:
  Phase 1 — Exact: cleaned_login == u_td_user_id (case-insensitive)
  Phase 2 — Substring: u_td_user_id appears inside cleaned_login
            UIDs sorted longest-first (most specific), logins longest-first.
            Greedy 1:1 assignment; once matched, both are removed from the pool.

Usage:
    python3 generate_identity_map.py data/raw
    python3 generate_identity_map.py data/raw --prefixes "us-" "corp-" --suffixes "_bank" "_svc"
    python3 generate_identity_map.py /path/to/csvs --workers 4 --max-memory-gb 2.0
"""

import argparse
import csv
import sys
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing

import pandas as pd


# ============================================================
# Configuration — edit these lists to match your environment
# ============================================================

# Known prefixes/suffixes on GitHub login names that should be stripped
# before matching. Case-insensitive. Override via CLI --prefixes / --suffixes.
LOGIN_PREFIXES_TO_STRIP = ["us-"]
LOGIN_SUFFIXES_TO_STRIP = ["_bank"]

# Encoding candidates ordered strict-to-permissive (same order as inspect_raw_data.py).
# latin-1 and iso-8859-1 accept any byte so they always succeed — keep them last.
ENCODINGS_TO_TRY = [
    "utf-8",
    "utf-8-sig",
    "cp1252",
    "latin-1",
    "iso-8859-1",
]

# Below this many total comparisons, skip multiprocessing overhead
PARALLEL_THRESHOLD = 500_000


# ============================================================
# Encoding detection + CSV reading (mirrors inspect_raw_data.py)
# ============================================================

def detect_encoding(path, sample_bytes=65536):
    """Try decoding the first 64KB with each encoding. Return the first that works."""
    raw = path.read_bytes()[:sample_bytes]
    for encoding in ENCODINGS_TO_TRY:
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def detect_delimiter(path, encoding):
    """Sniff the CSV delimiter from the first few KB."""
    with open(path, "r", encoding=encoding, errors="replace") as f:
        sample = f.read(8192)
    if not sample.strip():
        return ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def read_single_column(filepath, column_name):
    """
    Read one column from a CSV. Handles encoding detection with fallback.
    Returns a pandas Series of unique, non-null values.
    """
    filepath = Path(filepath)
    detected = detect_encoding(filepath)
    delimiter = detect_delimiter(filepath, detected)

    # Build ordered list: detected encoding first, then the rest
    candidates = [detected] + [e for e in ENCODINGS_TO_TRY if e != detected]

    for encoding in candidates:
        try:
            df = pd.read_csv(
                filepath, usecols=[column_name],
                encoding=encoding, delimiter=delimiter,
            )
            values = df[column_name].dropna().astype(str).drop_duplicates().reset_index(drop=True)
            print(f"  {filepath.name}: {len(values):,} unique '{column_name}' values "
                  f"(encoding: {encoding})")
            return values
        except (UnicodeDecodeError, UnicodeError):
            continue
        except ValueError:
            # Column not found — show what's available and exit
            for enc in candidates:
                try:
                    cols = pd.read_csv(filepath, nrows=0, encoding=enc, delimiter=delimiter).columns.tolist()
                    print(f"ERROR: Column '{column_name}' not found in {filepath.name}")
                    print(f"  Available columns: {cols}")
                    sys.exit(1)
                except Exception:
                    continue
            print(f"ERROR: Could not read headers from {filepath.name}")
            sys.exit(1)

    print(f"ERROR: All encodings failed for {filepath.name}")
    sys.exit(1)


# ============================================================
# Login cleaning
# ============================================================

def clean_login(login, prefixes, suffixes):
    """
    Strip one known prefix and one known suffix from a login string.
    Case-insensitive check, but preserves the remaining characters' case.
    """
    lower = login.lower()
    for prefix in prefixes:
        if lower.startswith(prefix.lower()):
            login = login[len(prefix):]
            break
    lower = login.lower()
    for suffix in suffixes:
        if lower.endswith(suffix.lower()):
            login = login[:-len(suffix)]
            break
    return login


# ============================================================
# Parallel substring search
# ============================================================

def _find_candidates_chunk(uid_chunk, login_pairs):
    """
    For a chunk of (uid_original, uid_lower) pairs, find all logins
    where uid_lower is a substring of cleaned_login_lower.

    Returns dict: {uid_original: [original_login, ...]}
    Candidate lists preserve the login_pairs ordering (longest login first).
    """
    results = {}
    for uid_original, uid_lower in uid_chunk:
        candidates = []
        for original_login, cleaned_lower in login_pairs:
            if uid_lower in cleaned_lower:
                candidates.append(original_login)
        if candidates:
            results[uid_original] = candidates
    return results


# Picklable top-level wrapper for ProcessPoolExecutor
def _worker(args):
    return _find_candidates_chunk(*args)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Generate identity mapping between GitHub logins and ServiceNow user IDs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 generate_identity_map.py data/raw
  python3 generate_identity_map.py data/raw --prefixes "us-" "corp-" --suffixes "_bank"
  python3 generate_identity_map.py /mnt/data --workers 4 --max-memory-gb 2
""",
    )
    parser.add_argument("target_folder",
                        help="Folder containing github_users.csv and sys_user.csv. "
                             "Output files will be written here.")
    parser.add_argument("--prefixes", nargs="*", default=LOGIN_PREFIXES_TO_STRIP,
                        help=f"Prefixes to strip from login column (default: {LOGIN_PREFIXES_TO_STRIP})")
    parser.add_argument("--suffixes", nargs="*", default=LOGIN_SUFFIXES_TO_STRIP,
                        help=f"Suffixes to strip from login column (default: {LOGIN_SUFFIXES_TO_STRIP})")
    parser.add_argument("--max-memory-gb", type=float, default=3.0,
                        help="Memory budget in GB — controls chunk sizing (default: 3.0)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Parallel workers for substring search (default: CPU count - 1)")
    args = parser.parse_args()

    target = Path(args.target_folder)
    if not target.is_dir():
        print(f"ERROR: '{target}' is not a directory")
        sys.exit(1)

    github_file = target / "github_users.csv"
    sys_file = target / "sys_user.csv"
    for f in [github_file, sys_file]:
        if not f.exists():
            print(f"ERROR: Required file not found: {f}")
            sys.exit(1)

    max_workers = args.workers or max(1, multiprocessing.cpu_count() - 1)
    prefixes = args.prefixes or []
    suffixes = args.suffixes or []

    print("=" * 60)
    print("Identity Map Generator")
    print("=" * 60)
    print(f"  Target folder : {target}")
    print(f"  Prefixes      : {prefixes}")
    print(f"  Suffixes      : {suffixes}")
    print(f"  Workers       : {max_workers}")
    print(f"  Memory budget : {args.max_memory_gb:.1f} GB")
    print()

    # ------------------------------------------------------------------
    # Step 1: Load data
    # ------------------------------------------------------------------
    print("Loading data...")
    t0 = time.time()
    logins_raw = read_single_column(github_file, "login")
    uids_raw = read_single_column(sys_file, "u_td_user_id")
    print(f"  Loaded in {time.time() - t0:.1f}s\n")

    # ------------------------------------------------------------------
    # Step 2: Clean logins (strip prefixes/suffixes)
    # ------------------------------------------------------------------
    login_cleaned = logins_raw.apply(lambda x: clean_login(x, prefixes, suffixes))
    login_cleaned_lower = login_cleaned.str.lower()

    # Build a DataFrame so we can track original <-> cleaned <-> lower
    login_df = pd.DataFrame({
        "login": logins_raw,
        "cleaned": login_cleaned,
        "cleaned_lower": login_cleaned_lower,
    })
    # Drop logins that became empty after stripping
    empty_after_strip = (login_df["cleaned"].str.len() == 0).sum()
    login_df = login_df[login_df["cleaned"].str.len() > 0].reset_index(drop=True)

    uid_df = pd.DataFrame({
        "u_td_user_id": uids_raw,
        "uid_lower": uids_raw.str.lower(),
    })
    # Drop empty UIDs
    uid_df = uid_df[uid_df["uid_lower"].str.len() > 0].reset_index(drop=True)

    if prefixes or suffixes:
        changed = (logins_raw != login_cleaned).sum()
        print(f"Prefix/suffix stripping changed {changed:,} of {len(logins_raw):,} logins")
    if empty_after_strip > 0:
        print(f"[WARN] {empty_after_strip:,} logins became empty after stripping and were dropped")
    print(f"Working set: {len(login_df):,} logins x {len(uid_df):,} UIDs\n")

    matched_logins = set()
    matched_uids = set()
    all_matches = []

    # ------------------------------------------------------------------
    # Step 3: Phase 1 — Exact matches (case-insensitive)
    # ------------------------------------------------------------------
    print("Phase 1: Exact matching...")
    t1 = time.time()

    # Build lookup: cleaned_lower -> original login (first occurrence)
    exact_lookup = {}
    for _, row in login_df.iterrows():
        key = row["cleaned_lower"]
        if key not in exact_lookup:
            exact_lookup[key] = row["login"]

    for _, row in uid_df.iterrows():
        uid_lower = row["uid_lower"]
        if uid_lower in exact_lookup:
            original_login = exact_lookup[uid_lower]
            all_matches.append({
                "login": original_login,
                "u_td_user_id": row["u_td_user_id"],
                "match_type": "exact",
            })
            matched_logins.add(original_login)
            matched_uids.add(row["u_td_user_id"])

    exact_count = len(all_matches)
    print(f"  Exact matches: {exact_count:,}  ({time.time() - t1:.1f}s)\n")

    # ------------------------------------------------------------------
    # Step 4: Phase 2 — Substring matching (parallelized)
    # ------------------------------------------------------------------
    remaining_login_df = login_df[~login_df["login"].isin(matched_logins)].copy()
    remaining_uid_df = uid_df[~uid_df["u_td_user_id"].isin(matched_uids)].copy()

    n_logins = len(remaining_login_df)
    n_uids = len(remaining_uid_df)
    total_comparisons = n_logins * n_uids

    print(f"Phase 2: Substring matching ({n_uids:,} UIDs x {n_logins:,} logins "
          f"= {total_comparisons:,} comparisons)...")
    t2 = time.time()

    if n_uids > 0 and n_logins > 0:
        # Sort UIDs longest-first (most specific), logins longest-first
        remaining_uid_df["_len"] = remaining_uid_df["uid_lower"].str.len()
        remaining_uid_df = remaining_uid_df.sort_values("_len", ascending=False)
        remaining_uid_df = remaining_uid_df.drop(columns=["_len"])

        remaining_login_df["_len"] = remaining_login_df["cleaned_lower"].str.len()
        remaining_login_df = remaining_login_df.sort_values("_len", ascending=False)
        remaining_login_df = remaining_login_df.drop(columns=["_len"])

        uid_pairs = list(zip(remaining_uid_df["u_td_user_id"], remaining_uid_df["uid_lower"]))
        login_pairs = list(zip(remaining_login_df["login"], remaining_login_df["cleaned_lower"]))

        # Decide: parallel or single-threaded
        use_parallel = total_comparisons >= PARALLEL_THRESHOLD and max_workers > 1

        if use_parallel:
            # Size chunks so each worker gets a meaningful batch
            chunk_size = max(10, len(uid_pairs) // (max_workers * 4))
            # Reduce chunk size if memory is tight (rough estimate: 200 bytes per login pair per worker)
            max_mem_bytes = int(args.max_memory_gb * 1024**3)
            mem_per_worker = len(login_pairs) * 200
            max_concurrent = max(1, int(max_mem_bytes * 0.5 / mem_per_worker))
            effective_workers = min(max_workers, max_concurrent)

            uid_chunks = [uid_pairs[i:i + chunk_size] for i in range(0, len(uid_pairs), chunk_size)]
            print(f"  Using {effective_workers} workers, {len(uid_chunks)} chunks "
                  f"of ~{chunk_size} UIDs each")

            all_candidates = {}
            with ProcessPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(_worker, (chunk, login_pairs))
                    for chunk in uid_chunks
                ]
                done = 0
                for future in as_completed(futures):
                    chunk_result = future.result()
                    all_candidates.update(chunk_result)
                    done += 1
                    if done % max(1, len(futures) // 5) == 0 or done == len(futures):
                        print(f"  ... {done}/{len(futures)} chunks complete")
        else:
            if total_comparisons > 0:
                print(f"  Single-threaded (below {PARALLEL_THRESHOLD:,} threshold)")
            all_candidates = _find_candidates_chunk(uid_pairs, login_pairs)

        print(f"  UIDs with candidates: {len(all_candidates):,}")

        # Greedy 1:1 assignment — UIDs longest-first, pick first unassigned login
        assigned_logins = set()
        substring_count = 0

        for uid_original, _ in uid_pairs:
            if uid_original not in all_candidates:
                continue
            for login_candidate in all_candidates[uid_original]:
                if login_candidate not in assigned_logins:
                    all_matches.append({
                        "login": login_candidate,
                        "u_td_user_id": uid_original,
                        "match_type": "substring",
                    })
                    assigned_logins.add(login_candidate)
                    matched_logins.add(login_candidate)
                    matched_uids.add(uid_original)
                    substring_count += 1
                    break

        print(f"  Substring matches: {substring_count:,}  ({time.time() - t2:.1f}s)\n")
    else:
        substring_count = 0
        print(f"  Nothing to match (one side empty)\n")

    # ------------------------------------------------------------------
    # Step 5: Write output
    # ------------------------------------------------------------------
    total_matched = len(all_matches)

    # Matched pairs
    output_matched = target / "identity_mapping.csv"
    if total_matched > 0:
        matched_df = pd.DataFrame(all_matches)[["login", "u_td_user_id", "match_type"]]
        matched_df.to_csv(output_matched, index=False)
    else:
        pd.DataFrame(columns=["login", "u_td_user_id", "match_type"]).to_csv(
            output_matched, index=False)

    # Unmatched users (both sides, separate columns)
    unmatched_logins = sorted(login_df[~login_df["login"].isin(matched_logins)]["login"].tolist())
    unmatched_uids = sorted(uid_df[~uid_df["u_td_user_id"].isin(matched_uids)]["u_td_user_id"].tolist())

    max_rows = max(len(unmatched_logins), len(unmatched_uids))
    unmatched_logins_padded = unmatched_logins + [""] * (max_rows - len(unmatched_logins))
    unmatched_uids_padded = unmatched_uids + [""] * (max_rows - len(unmatched_uids))

    output_unmatched = target / "identity_unmatched.csv"
    pd.DataFrame({
        "unmatched_login": unmatched_logins_padded,
        "unmatched_u_td_user_id": unmatched_uids_padded,
    }).to_csv(output_unmatched, index=False)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_logins = len(login_df)
    total_uids = len(uid_df)
    elapsed = time.time() - t0

    print("=" * 60)
    print("Results")
    print("=" * 60)
    print(f"  GitHub logins loaded : {total_logins:,}")
    print(f"  ServiceNow UIDs loaded: {total_uids:,}")
    print()
    print(f"  Exact matches        : {exact_count:,}")
    print(f"  Substring matches    : {substring_count:,}")
    print(f"  Total matched        : {total_matched:,}")
    print()
    print(f"  Unmatched logins     : {len(unmatched_logins):,} "
          f"({len(unmatched_logins) / total_logins * 100:.1f}%)" if total_logins > 0 else "")
    print(f"  Unmatched UIDs       : {len(unmatched_uids):,} "
          f"({len(unmatched_uids) / total_uids * 100:.1f}%)" if total_uids > 0 else "")
    print()
    print(f"  -> {output_matched}")
    print(f"  -> {output_unmatched}")
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
