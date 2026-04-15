#!/usr/bin/env python3
"""
Inspect raw CSV data exports and produce a schema report.

Traverses a target folder for .csv files, detects encoding, samples rows
to infer column types, and writes a markdown report. The report contains
only structural metadata (column names, types, null rates, value counts)
— never actual data values — so it is safe to transfer across an airgap.

Usage:
    python3 inspect_raw_data.py data/raw
    python3 inspect_raw_data.py data/raw --output data_inspection.md
    python3 inspect_raw_data.py /some/other/folder --sample-pct 10
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import numpy as np


# Encoding priority: strict encodings first, permissive ones last.
# latin-1 and iso-8859-1 accept any byte sequence so they always succeed —
# that's why they come after utf-8 and cp1252.
ENCODINGS_TO_TRY = [
    "utf-8",
    "utf-8-sig",
    "cp1252",
    "latin-1",
    "iso-8859-1",
    "cp437",
    "ascii",
]


def detect_encoding(path):
    """
    Try decoding the first 64KB with each encoding. Return the first that works.

    Note: this is a fast heuristic based only on the head of the file. A file
    can look like valid UTF-8 for its first 64KB and then have a latin-1 byte
    (e.g. 0xe9 = 'é') further in. Callers that actually *read* the file
    should use _read_csv_with_fallback so they can recover from that case.
    """
    sample_bytes = path.read_bytes()[:65536]
    for encoding in ENCODINGS_TO_TRY:
        try:
            sample_bytes.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"


def _read_csv_with_fallback(path, detected_encoding, delimiter, **read_kwargs):
    """
    Read a CSV, retrying with every candidate encoding if a decode error
    occurs. Necessary because detect_encoding() only samples the first 64KB,
    so the 'detected' encoding can still be wrong for the full file.

    Tries the detected encoding first (fast path), then the rest of
    ENCODINGS_TO_TRY. Since latin-1/iso-8859-1 accept any byte, the fallback
    is guaranteed to eventually succeed — worst case you get mojibake in
    some columns, but the inspector still produces a report.

    Returns (dataframe, encoding_that_actually_worked).
    """
    candidates = [detected_encoding] + [
        e for e in ENCODINGS_TO_TRY if e != detected_encoding
    ]

    last_error = None
    for encoding in candidates:
        try:
            df = pd.read_csv(path, encoding=encoding, delimiter=delimiter, **read_kwargs)
            return df, encoding
        except UnicodeDecodeError as error:
            last_error = error
            continue

    raise RuntimeError(
        f"All encodings failed: tried {candidates}. Last error: {last_error}"
    )


def detect_delimiter(path, encoding):
    """Sniff the CSV delimiter from the first few lines."""
    with open(path, "r", encoding=encoding, errors="replace") as f:
        sample_text = f.read(8192)

    if len(sample_text.strip()) == 0:
        return ","

    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=",;\t|")
        return dialect.delimiter
    except csv.Error:
        return ","


def count_rows(path, encoding):
    """Count data rows (excluding header) without loading into memory."""
    count = 0
    with open(path, "r", encoding=encoding, errors="replace") as f:
        next(f, None)  # skip header
        for _ in f:
            count += 1
    return count


def infer_column_type(series):
    """
    Classify a column's data type from a sample.
    Returns: integer, float, boolean, date/datetime, uuid, json/nested, text, or empty.
    """
    non_null = series.dropna()
    if len(non_null) == 0:
        return "empty"

    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"

    values = non_null.astype(str).str.strip()
    values = values[values != ""]
    if len(values) == 0:
        return "empty"

    bool_values = {"true", "false", "t", "f", "yes", "no", "0", "1"}
    if values.str.lower().isin(bool_values).mean() > 0.9:
        return "boolean"

    if values.str.match(r"^-?\d+$").mean() > 0.9:
        return "integer"

    if values.str.match(r"^-?\d+\.?\d*(e[+-]?\d+)?$", case=False).mean() > 0.9:
        return "float"

    date_patterns = [
        r"^\d{4}-\d{2}-\d{2}$",
        r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}",
        r"^\d{2}/\d{2}/\d{4}",
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
    ]
    for pattern in date_patterns:
        if values.str.match(pattern).mean() > 0.8:
            return "date/datetime"

    uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    if values.str.match(uuid_pattern, case=False).mean() > 0.8:
        return "uuid"

    if values.str.startswith(("{", "[")).mean() > 0.5:
        return "json/nested"

    return "text"


def compute_column_stats(series, col_type):
    """
    Compute stats for a column. Only structural metadata — no actual values.

    For numeric: min, max, mean (safe to share — these are aggregate stats).
    For dates: earliest/latest date (aggregate).
    For text: average string length and distinct count (no example values).
    For boolean: value distribution as percentages (no raw values).
    """
    non_null = series.dropna()
    null_pct = series.isna().sum() / len(series) * 100 if len(series) > 0 else 0

    col_stats = {
        "null_pct": f"{null_pct:.1f}%",
        "unique_in_sample": int(non_null.nunique()),
    }

    if col_type in ("integer", "float"):
        numeric = pd.to_numeric(non_null, errors="coerce").dropna()
        if len(numeric) > 0:
            col_stats["min"] = f"{numeric.min():.4g}"
            col_stats["max"] = f"{numeric.max():.4g}"
            col_stats["mean"] = f"{numeric.mean():.4g}"

    elif col_type in ("date/datetime", "datetime"):
        parsed = pd.to_datetime(non_null, errors="coerce").dropna()
        if len(parsed) > 0:
            col_stats["earliest"] = str(parsed.min().date())
            col_stats["latest"] = str(parsed.max().date())

    elif col_type == "text":
        lengths = non_null.astype(str).str.len()
        col_stats["avg_length"] = f"{lengths.mean():.0f} chars"
        col_stats["min_length"] = int(lengths.min())
        col_stats["max_length"] = int(lengths.max())

    elif col_type == "boolean":
        col_stats["distinct_values"] = non_null.astype(str).str.lower().nunique()

    elif col_type == "uuid":
        col_stats["format"] = "UUID v4"

    elif col_type == "json/nested":
        col_stats["avg_length"] = f"{non_null.astype(str).str.len().mean():.0f} chars"

    return col_stats


def inspect_csv(path, sample_pct=5.0):
    """Inspect a single CSV file. Returns a dict of structural metadata."""
    result = {"file": path.name, "path": str(path), "errors": []}

    # Handle empty files
    size_bytes = path.stat().st_size
    if size_bytes == 0:
        result["encoding"] = "n/a"
        result["delimiter"] = "n/a"
        result["total_rows"] = 0
        result["file_size"] = "0 KB"
        result["column_count"] = 0
        result["errors"].append("File is empty (0 bytes)")
        return result

    encoding = detect_encoding(path)
    result["encoding"] = encoding

    delimiter = detect_delimiter(path, encoding)
    result["delimiter"] = repr(delimiter)

    try:
        total_rows = count_rows(path, encoding)
    except Exception as e:
        result["errors"].append(f"Row count failed: {e}")
        total_rows = 0
    result["total_rows"] = total_rows

    if size_bytes > 1_048_576:
        result["file_size"] = f"{size_bytes / 1_048_576:.1f} MB"
    else:
        result["file_size"] = f"{size_bytes / 1024:.1f} KB"

    # Read headers
    try:
        headers_df = pd.read_csv(path, nrows=0, encoding=encoding,
                                  delimiter=delimiter, on_bad_lines="skip")
        columns = headers_df.columns.tolist()
    except Exception as e:
        result["errors"].append(f"Header read failed: {e}")
        return result

    result["column_count"] = len(columns)

    # Handle header-only files (no data rows)
    if total_rows == 0:
        result["sampled_rows"] = 0
        result["columns"] = [
            {"name": col, "inferred_type": "empty", "null_pct": "n/a", "unique_in_sample": 0}
            for col in columns
        ]
        return result

    # Sample rows
    sample_n = max(50, int(total_rows * sample_pct / 100))

    if total_rows <= 1000:
        try:
            df, actual_encoding = _read_csv_with_fallback(
                path, encoding, delimiter,
                on_bad_lines="skip", low_memory=False,
            )
        except Exception as e:
            result["errors"].append(f"Full read failed: {e}")
            return result
    else:
        skip_probability = 1 - (sample_n / total_rows)
        rng = np.random.RandomState(42)
        try:
            df, actual_encoding = _read_csv_with_fallback(
                path, encoding, delimiter,
                on_bad_lines="skip", low_memory=False,
                skiprows=lambda row_index: row_index > 0 and rng.random() < skip_probability,
            )
        except Exception as e:
            result["errors"].append(f"Sample read failed: {e}")
            return result

    # If the fallback had to pick a different encoding than detect_encoding
    # guessed, record that so the report shows the one that actually worked.
    if actual_encoding != encoding:
        result["errors"].append(
            f"Encoding detection suggested '{encoding}' (based on first 64KB) "
            f"but full read required '{actual_encoding}'"
        )
        result["encoding"] = actual_encoding

    result["sampled_rows"] = len(df)

    col_details = []
    for col in df.columns:
        col_type = infer_column_type(df[col])
        stats = compute_column_stats(df[col], col_type)
        col_details.append({
            "name": col,
            "inferred_type": col_type,
            **stats,
        })

    result["columns"] = col_details
    return result


def inspect_folder(folder, sample_pct=5.0):
    """Find all CSVs in folder and subfolders, inspect each one."""
    csv_files = sorted(folder.rglob("*.csv"))

    if not csv_files:
        print(f"No .csv files found in {folder}")
        return []

    print(f"Found {len(csv_files)} CSV file(s) in {folder}\n")

    results = []
    for i, path in enumerate(csv_files, 1):
        print(f"  [{i}/{len(csv_files)}] Inspecting {path.name}...", end="", flush=True)
        try:
            result = inspect_csv(path, sample_pct)
        except Exception as e:
            result = {"file": path.name, "path": str(path),
                      "errors": [f"Inspection failed: {e}"],
                      "total_rows": 0, "column_count": 0,
                      "encoding": "unknown", "file_size": "unknown"}
        results.append(result)
        print(f" {result['total_rows']:,} rows, {result.get('column_count', '?')} columns")

    return results


def format_stats(stats):
    """Format the stats dict into a compact string for the Details column."""
    skip_keys = {"null_pct", "unique_in_sample", "name", "inferred_type"}
    parts = []
    for key, value in stats.items():
        if key in skip_keys:
            continue
        parts.append(f"{key}={value}")
    if parts:
        return "; ".join(parts)
    return ""


def write_markdown(results, output_path, folder):
    """Write the inspection results as a markdown file."""
    lines = []

    lines.append("# Raw Data Inspection Report")
    lines.append("")
    lines.append("> This report contains only structural metadata (column names, types,")
    lines.append("> null rates, aggregate stats). No actual data values are included.")
    lines.append("")
    lines.append(f"**Source folder:** `{folder}`")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Files inspected:** {len(results)}")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| CSV File | Rows | Columns | Size | Encoding |")
    lines.append("|----------|------|---------|------|----------|")
    for result in results:
        lines.append(
            f"| `{result['file']}` "
            f"| {result['total_rows']:,} "
            f"| {result.get('column_count', '?')} "
            f"| {result.get('file_size', '?')} "
            f"| {result['encoding']} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-table details
    for result in results:
        table_name = Path(result["file"]).stem

        lines.append(f"## `{table_name}`")
        lines.append("")
        lines.append(f"- **File:** `{result['file']}`")
        lines.append(f"- **Total rows:** {result['total_rows']:,}")
        lines.append(f"- **Columns:** {result.get('column_count', '?')}")
        lines.append(f"- **File size:** {result.get('file_size', '?')}")
        lines.append(f"- **Encoding:** {result['encoding']}")
        lines.append(f"- **Delimiter:** {result.get('delimiter', ',')}")
        lines.append(f"- **Rows sampled:** {result.get('sampled_rows', '?')}")
        lines.append("")

        if result.get("errors"):
            for err in result["errors"]:
                lines.append(f"> **Error:** {err}")
            lines.append("")

        columns = result.get("columns", [])
        if columns:
            lines.append("| # | Column Name | Type | Null % | Unique (sample) | Details |")
            lines.append("|---|-------------|------|--------|-----------------|---------|")
            for i, col in enumerate(columns, 1):
                detail_str = format_stats(col)
                lines.append(
                    f"| {i} "
                    f"| `{col['name']}` "
                    f"| {col['inferred_type']} "
                    f"| {col.get('null_pct', '?')} "
                    f"| {col.get('unique_in_sample', '?')} "
                    f"| {detail_str} |"
                )
            lines.append("")

        lines.append("---")
        lines.append("")

    # Quick reference
    lines.append("## Quick Reference — All Column Names by Table")
    lines.append("")
    lines.append("Use this to verify column names match what the analysis code expects.")
    lines.append("")
    for result in results:
        table_name = Path(result["file"]).stem
        columns = result.get("columns", [])
        if columns:
            col_list = ", ".join(f"`{c['name']}`" for c in columns)
            lines.append(f"**`{table_name}`:** {col_list}")
            lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Inspect raw CSV exports and produce a markdown schema report.",
        epilog="Example: python3 inspect_raw_data.py data/raw",
    )
    parser.add_argument("folder", type=str,
                        help="Target folder to scan for .csv files")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Output path (default: <folder>/data_inspection.md)")
    parser.add_argument("--sample-pct", type=float, default=5.0,
                        help="Percent of rows to sample for type inference (default: 5)")

    args = parser.parse_args()
    folder = Path(args.folder).resolve()

    if not folder.exists():
        print(f"ERROR: Folder not found: {folder}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else folder / "data_inspection.md"

    print(f"Scanning: {folder}")
    print(f"Sample:   {args.sample_pct}% of rows")
    print(f"Output:   {output_path}")
    print()

    results = inspect_folder(folder, sample_pct=args.sample_pct)

    if not results:
        sys.exit(1)

    write_markdown(results, output_path, folder)
    print(f"\nReport written to: {output_path}")


if __name__ == "__main__":
    main()
