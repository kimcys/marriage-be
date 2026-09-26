"""Fake OCR CLI used by integration tests.

Writes an XLSX shaped like the real marriage-ocr CLI output: one row per
record, mixing business-data columns with diagnostic/metadata columns
(Confidence, Source Page, Review Reason, ...). No JSON sidecar is produced,
matching the real CLI, which only ever emits the XLSX.

Also fakes the two OneDrive-ingestion subprocess calls
(onedrive/runner.py::OneDriveFetchRunner) -- `onedrive fetch-public` and
`classify` -- so scripts/docker_e2e.py can exercise the real (and today,
only) OneDrive-link ingestion path without needing a real OneDrive URL or
network access in CI.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

import openpyxl

_HEADER = ["full_name", "Confidence", "Review Reason", "Source File", "Source Page", "Source Record"]
_ROWS = [
    ["Ada Lovelace", 0.97, "", "register.pdf", 1, "record_001"],
    ["Grace Hopper", 0.95, "", "register.pdf", 1, "record_002"],
]

_TYPED_HEADER = ["Nama Suami", "Nama Isteri", "Source File", "Processing Status", "Review Required"]
_TYPED_ROWS = [
    ["Ali Bin Abu", "Siti Binti Ali", "borang4b.pdf", "OK", "false"],
]


def _write_fake_xlsx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(_HEADER)
    for row in _ROWS:
        worksheet.append(row)
    workbook.save(path)


def _write_fake_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(_TYPED_HEADER)
        writer.writerows(_TYPED_ROWS)


def _handle_classify(argv: list[str]) -> int:
    """Always reports the fetched file as a routable typed Nikah (modern)
    document -- (doc_type, record_type, layout_variant) = ("typed", "nikah",
    "modern") maps to DocumentType.TYPED_NIKAH_MODERN in
    onedrive/classification.py::CLASSIFICATION_TO_DOCUMENT_TYPE, which then
    runs through `process-typed` below, same as every other fake-CLI path
    here. The real triage.py inspects the file's actual content; this fake
    doesn't need to since docker_e2e.py's fake "fetched" file is a
    placeholder anyway."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--page-ocr-output")
    parser.parse_args(argv)
    print(
        json.dumps(
            {
                "doc_type": "typed",
                "record_type": "nikah",
                "layout_variant": "modern",
                "status": "ROUTABLE",
                "config_path": "config/typed_borang4b.yaml",
            }
        )
    )
    return 0


def _handle_onedrive(argv: list[str]) -> int:
    if not argv:
        print("missing onedrive subcommand", file=sys.stderr)
        return 2
    subcommand, rest = argv[0], argv[1:]
    if subcommand not in {"fetch-public", "fetch"}:
        print(f"unsupported onedrive subcommand: {subcommand}", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--dest", required=True)
    args = parser.parse_args(rest)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    # Only this file's presence and extension matter to the OneDrive
    # ingestion pipeline (onedrive/service.py::run_onedrive_fetch) -- its
    # content is never actually read since _handle_classify above doesn't
    # inspect the file it's pointed at either.
    (dest / "register.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "classify":
        return _handle_classify(argv[1:])
    if argv and argv[0] == "onedrive":
        return _handle_onedrive(argv[1:])

    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--debug")
    parser.add_argument("--config")
    parser.add_argument("--reset-output", action="store_true")
    parser.add_argument("--page1-ocr")
    args = parser.parse_args(argv)

    mode = os.environ.get("FAKE_OCR_MODE", "success")
    if args.command not in {"process", "process-typed"}:
        print("unsupported command", file=sys.stderr)
        return 2

    if mode == "success":
        if args.command == "process-typed":
            _write_fake_csv(Path(args.output))
        else:
            _write_fake_xlsx(Path(args.output))
        return 0
    if mode == "fail-named-input":
        # Fails only for a specific input filename (set via FAKE_OCR_FAIL_INPUT_NAME),
        # so a test can exercise one failing page among several succeeding ones.
        target_name = os.environ.get("FAKE_OCR_FAIL_INPUT_NAME", "")
        if args.input and Path(args.input).name == target_name:
            print("\x1b[31mfake ocr failed for targeted input\x1b[0m", file=sys.stderr)
            return 1
        if args.command == "process-typed":
            _write_fake_csv(Path(args.output))
        else:
            _write_fake_xlsx(Path(args.output))
        return 0
    if mode == "failure":
        print("\x1b[31mfake ocr failed\x1b[0m", file=sys.stderr)
        return 1
    if mode == "no-output":
        return 0
    if mode == "timeout":
        time.sleep(10)
        return 0
    print("unknown mode", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
