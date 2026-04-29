#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any


def _default_raw_path() -> Path:
    return Path(tempfile.gettempdir()) / time.strftime("gemini-stream-%Y%m%d-%H%M%S.jsonl")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture or inspect Gemini CLI stream-json output and reconstruct "
            "assistant text."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="Prompt to run through Gemini CLI.")
    source.add_argument("--input", type=Path, help="Existing raw JSONL file to inspect.")
    parser.add_argument("--raw", type=Path, default=None, help="Raw JSONL output path.")
    parser.add_argument("--gemini-bin", default="gemini", help="Path to gemini binary.")
    parser.add_argument("--model", default=None, help="Gemini model passed with -m.")
    parser.add_argument("--working-dir", type=Path, default=None, help="Process cwd.")
    parser.add_argument(
        "--approval-mode",
        default=None,
        help="Optional Gemini CLI approval mode, for example yolo.",
    )
    parser.add_argument(
        "--skip-trust",
        action="store_true",
        help="Pass --skip-trust to Gemini CLI.",
    )
    parser.add_argument(
        "--include-directory",
        action="append",
        default=[],
        help="Directory passed to --include-directories. Can be repeated.",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="Print one short line per stream event while inspecting.",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=120,
        help="Characters shown for each message event when --events is enabled.",
    )
    return parser


def _drain_stderr(pipe: Any, path: Path) -> None:
    with path.open("w", encoding="utf-8", errors="replace") as stderr_file:
        for line in pipe:
            stderr_file.write(line)
            stderr_file.flush()
            sys.stderr.write(line)


def _run_gemini(args: argparse.Namespace, raw_path: Path) -> int:
    command = [args.gemini_bin]
    if args.model:
        command.extend(["-m", args.model])
    command.extend(["-o", "stream-json", "-p", args.prompt])
    if args.skip_trust:
        command.append("--skip-trust")
    if args.approval_mode:
        command.extend(["--approval-mode", args.approval_mode])
    for directory in args.include_directory:
        command.extend(["--include-directories", directory])

    stderr_path = raw_path.with_suffix(".stderr.txt")
    process = subprocess.Popen(
        command,
        cwd=str(args.working_dir) if args.working_dir else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stderr_thread = threading.Thread(
        target=_drain_stderr,
        args=(process.stderr, stderr_path),
        daemon=True,
    )
    stderr_thread.start()

    with raw_path.open("w", encoding="utf-8", errors="replace") as raw_file:
        for line in process.stdout:
            raw_file.write(line)
            raw_file.flush()

    returncode = process.wait()
    stderr_thread.join(timeout=5)
    print(f"stderr file: {stderr_path}")
    return returncode


def _snapshot_append(snapshot: str, content: str, delta: Any) -> tuple[str, bool]:
    if delta:
        return snapshot + content, False
    if content == snapshot:
        return snapshot, False
    if content.startswith(snapshot):
        return snapshot + content[len(snapshot) :], False
    return content, True


def _inspect_jsonl(
    raw_path: Path,
    *,
    print_events: bool,
    preview_chars: int,
) -> int:
    counts: Counter[str] = Counter()
    delta_counts: Counter[str] = Counter()
    concat_parts: list[str] = []
    snapshot = ""
    snapshot_resets = 0
    non_json = 0

    for line_number, line in enumerate(
        raw_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            non_json += 1
            if print_events:
                print(f"{line_number}: NON_JSON {exc}: {line[:preview_chars]!r}")
            continue
        if not isinstance(event, dict):
            counts["json_non_object"] += 1
            continue

        event_type = str(event.get("type", ""))
        counts[event_type or "<missing>"] += 1
        role = event.get("role")
        content = event.get("content")
        delta = event.get("delta")

        if event_type == "message" and role == "assistant" and isinstance(content, str):
            concat_parts.append(content)
            snapshot, reset = _snapshot_append(snapshot, content, delta)
            snapshot_resets += int(reset)
            delta_counts[str(delta)] += 1
            if print_events:
                print(
                    f"{line_number}: message delta={delta!r} "
                    f"len={len(content)} repr={content[:preview_chars]!r}"
                )
            continue

        if print_events:
            keys = ", ".join(event.keys())
            print(f"{line_number}: type={event_type!r} keys=[{keys}]")

    concat_text = "".join(concat_parts)
    concat_path = raw_path.with_suffix(".concat.txt")
    snapshot_path = raw_path.with_suffix(".snapshot.txt")
    concat_path.write_text(concat_text, encoding="utf-8")
    snapshot_path.write_text(snapshot, encoding="utf-8")

    print("\n--- SUMMARY ---")
    print(f"raw file: {raw_path}")
    print(f"assistant concat chars: {len(concat_text)}")
    print(f"assistant snapshot-aware chars: {len(snapshot)}")
    print(f"assistant message events: {len(concat_parts)}")
    print(f"snapshot resets: {snapshot_resets}")
    print(f"non-json lines: {non_json}")
    print("event counts:")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type}: {count}")
    print("assistant delta counts:")
    for delta_value, count in sorted(delta_counts.items()):
        print(f"  {delta_value}: {count}")
    print(f"saved concat: {concat_path}")
    print(f"saved snapshot: {snapshot_path}")
    return 0


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    raw_path = args.input or args.raw or _default_raw_path()
    raw_path = raw_path.expanduser().resolve()
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    returncode = 0
    if args.prompt is not None:
        returncode = _run_gemini(args, raw_path)
        if returncode:
            print(f"gemini exited with code {returncode}", file=sys.stderr)

    inspect_code = _inspect_jsonl(
        raw_path,
        print_events=args.events,
        preview_chars=max(20, args.preview_chars),
    )
    return returncode or inspect_code


if __name__ == "__main__":
    raise SystemExit(main())
