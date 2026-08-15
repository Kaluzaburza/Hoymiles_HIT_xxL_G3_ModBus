#!/usr/bin/env python3
"""Analyze up to hundreds of Hoymiles diagnostic ZIP bundles offline."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys

from diagnostics_analysis.analyzer import analyze_inputs
from diagnostics_analysis.archive import (
    DEFAULT_LIMITS,
    ArchiveReadError,
)
from diagnostics_analysis.outputs import write_analysis_outputs


FAIL_RANK = {"none": 99, "critical": 3, "error": 2, "warning": 1, "info": 0}
SEVERITY_RANK = {"critical": 3, "error": 2, "warning": 1, "info": 0}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline longitudinal analysis of Hoymiles support ZIP bundles. "
            "No files are extracted and no network connection is used."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="Diagnostic ZIP file or directory containing ZIP files",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        type=Path,
        help="Directory for JSON, CSV, Markdown and HTML reports",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="Do not scan input directories recursively",
    )
    parser.add_argument(
        "--max-archives",
        type=int,
        default=DEFAULT_LIMITS.max_archives,
        help=f"Hard archive-count limit (default: {DEFAULT_LIMITS.max_archives})",
    )
    parser.add_argument(
        "--include-source-paths",
        action="store_true",
        help="Write opt-in source_map.csv containing local input paths",
    )
    parser.add_argument(
        "--include-anonymous-id",
        action="store_true",
        help="Include full anonymous UUID instead of only its stable hashed alias",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only known analyzer output files that already exist",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "critical", "error", "warning", "info"),
        default="none",
        help="Return exit code 10 when a finding reaches this severity",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_archives <= 0:
        print("ERROR: --max-archives must be positive", file=sys.stderr)
        return 2
    limits = replace(DEFAULT_LIMITS, max_archives=args.max_archives)
    try:
        summary = analyze_inputs(
            args.inputs,
            recursive=not args.no_recursive,
            limits=limits,
            include_source_paths=args.include_source_paths,
            include_anonymous_id=args.include_anonymous_id,
        )
    except ArchiveReadError as err:
        print(f"ERROR {err}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as err:
        print(f"ERROR: {type(err).__name__}: {err}", file=sys.stderr)
        return 2

    try:
        manifest = write_analysis_outputs(
            summary,
            args.output,
            force=args.force,
        )
    except (OSError, FileExistsError) as err:
        print(f"ERROR writing output: {err}", file=sys.stderr)
        return 4

    totals = summary["totals"]
    print(
        "Analyzed "
        f"{totals['accepted_or_partial_archives']}/{totals['discovered_archives']} "
        f"archives from {totals['installations']} installations; "
        f"{totals['finding_groups']} finding groups."
    )
    print(f"Reports: {args.output.resolve()}")
    print(
        "Files: "
        + ", ".join(str(item["name"]) for item in manifest)
    )

    accepted = int(totals["accepted_or_partial_archives"])
    rejected = int(totals["rejected_archives"])
    if accepted == 0:
        return 3
    threshold = FAIL_RANK[args.fail_on]
    if args.fail_on != "none" and any(
        SEVERITY_RANK.get(str(finding.get("severity")), -1) >= threshold
        for finding in summary.get("findings", [])
    ):
        return 10
    if rejected:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
