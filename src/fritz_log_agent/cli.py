"""CLI for downloading Fritz!Box logs and running in agent mode."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime
from getpass import getpass
from typing import Iterable

from dotenv import load_dotenv

from .client import FritzClient, LogEntry

LOGGER = logging.getLogger("fritz_log_agent")


def _read_credentials(
        username: str | None,
        password: str | None,
) -> tuple[str, str]:
    username = username or os.environ.get("FRITZBOX_USERNAME")
    password = password or os.environ.get("FRITZBOX_PASSWORD")

    if not username:
        username = input("Fritz!Box username: ").strip()
    if not password:
        password = getpass("Fritz!Box password: ").strip()

    if not username or not password:
        raise RuntimeError("Missing username or password")
    return username, password


def _entries_to_lines(entries: Iterable[LogEntry], fmt: str) -> Iterable[str]:
    if fmt == "text":
        for entry in entries:
            yield entry.to_text_line()
    else:
        for entry in entries:
            yield json.dumps(entry.to_json(), ensure_ascii=True)


def _emit_output(lines: Iterable[str], output: str) -> None:
    normalized = output.strip().lower()
    if normalized in {"-", "stdout"}:
        for line in lines:
            print(line)
        return

    with open(output, "a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(f"{line}\n")


def _emit_payload(payload: dict, output: str) -> None:
    line = json.dumps(payload, ensure_ascii=True)
    normalized = output.strip().lower()
    if normalized in {"-", "stdout"}:
        print(line)
        return

    with open(output, "w", encoding="utf-8") as handle:
        handle.write(f"{line}\n")


_GROUPED_SUFFIX_RE = re.compile(
    r"\s*\[(\d+)\s+Meldung(?:en)?\s+seit\s+(\d{2}\.\d{2}\.\d{2})\s+(\d{2}:\d{2}:\d{2})\]\s*$"
)


def _parse_grouped_suffix(message: str) -> tuple[str, int | None, datetime | None]:
    match = _GROUPED_SUFFIX_RE.search(message)
    if not match:
        return message, None, None
    count = int(match.group(1))
    date_str = match.group(2)
    time_str = match.group(3)
    try:
        since = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%y %H:%M:%S")
    except ValueError:
        return message, None, None
    cleaned = message[: match.start()].rstrip()
    return cleaned, count, since


def _normalize_agent_entry(entry: LogEntry) -> LogEntry:
    message, count, since = _parse_grouped_suffix(entry.message)
    if count is None and since is None:
        return entry
    return LogEntry(
        timestamp=entry.timestamp,
        group=entry.group,
        entry_id=entry.entry_id,
        message=message,
        group_count=count,
        group_since=since,
    )


def _run_once(client: FritzClient, args: argparse.Namespace) -> int:
    entries, payload = client.fetch_log_with_retry()
    if args.print_payload:
        _emit_payload(payload, args.output)
        return 0
    entries_sorted = sorted(entries, key=lambda entry: entry.timestamp)
    _emit_output(_entries_to_lines(entries_sorted, args.output_format), args.output)
    return 0


def _run_agent(client: FritzClient, args: argparse.Namespace) -> int:
    last_timestamp = datetime.fromtimestamp(0)
    last_signatures: set[str] = set()

    while True:
        try:
            entries, _payload = client.fetch_log_with_retry()
        except Exception as exc:
            LOGGER.error("Fetch failed: %s", exc)
            time.sleep(args.interval)
            continue

        entries_sorted = sorted(entries, key=lambda entry: entry.timestamp)
        new_entries: list[LogEntry] = []
        for entry in entries_sorted:
            normalized_entry = _normalize_agent_entry(entry)
            if normalized_entry.timestamp > last_timestamp:
                last_timestamp = normalized_entry.timestamp
                last_signatures = {_entry_signature(normalized_entry)}
                new_entries.append(normalized_entry)
            elif normalized_entry.timestamp == last_timestamp:
                signature = _entry_signature(normalized_entry)
                if signature not in last_signatures:
                    last_signatures.add(signature)
                    new_entries.append(normalized_entry)

        if new_entries:
            _emit_output(_entries_to_lines(new_entries, args.output_format), args.output)

        time.sleep(args.interval)


def _entry_signature(entry: LogEntry) -> str:
    return f"{entry.group}|{entry.entry_id}|{entry.message}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Fritz!Box logs and run in agent mode.")
    parser.add_argument("--base-url", default="http://fritz.box", help="Fritz!Box base URL")
    parser.add_argument("--username", help="Fritz!Box username")
    parser.add_argument("--password", help="Fritz!Box password")
    parser.add_argument(
        "--output-format",
        default="jsonl",
        choices=["jsonl", "text"],
        help="Emit entries in this format",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Write output to a file path or stdout ('-' or 'stdout')",
    )
    parser.add_argument(
        "--print-payload",
        action="store_true",
        help="Print the full JSON response once (one-shot mode only)",
    )
    parser.add_argument("--agent", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds for agent mode",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.agent and args.print_payload:
        parser.error("--print-payload is only supported in one-shot mode")

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    load_dotenv()
    username, password = _read_credentials(args.username, args.password)

    client = FritzClient(
        base_url=args.base_url,
        username=username,
        password=password,
        timeout=args.timeout,
    )

    if args.agent:
        LOGGER.info("Starting agent mode with interval %s seconds", args.interval)
        return _run_agent(client, args)

    return _run_once(client, args)


if __name__ == "__main__":
    raise SystemExit(main())
