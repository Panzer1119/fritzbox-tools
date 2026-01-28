"""CLI for downloading Fritz!Box logs and running in agent mode."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from getpass import getpass
from typing import Iterable

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


def _emit_stdout(entries: Iterable[LogEntry], fmt: str) -> None:
    if fmt == "text":
        for entry in entries:
            print(entry.to_text_line())
    else:
        for entry in entries:
            print(json.dumps(entry.to_json(), ensure_ascii=True))


def _run_once(client: FritzClient, args: argparse.Namespace) -> int:
    entries, _payload = client.fetch_log_with_retry()
    entries_sorted = sorted(entries, key=lambda entry: entry.timestamp)
    _emit_stdout(entries_sorted, args.stdout_format)
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
            if entry.timestamp > last_timestamp:
                last_timestamp = entry.timestamp
                last_signatures = {_entry_signature(entry)}
                new_entries.append(entry)
            elif entry.timestamp == last_timestamp:
                signature = _entry_signature(entry)
                if signature not in last_signatures:
                    last_signatures.add(signature)
                    new_entries.append(entry)

        if new_entries:
            _emit_stdout(new_entries, args.stdout_format)

        time.sleep(args.interval)


def _entry_signature(entry: LogEntry) -> str:
    return f"{entry.group}|{entry.entry_id}|{entry.message}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Fritz!Box logs and run in agent mode.")
    parser.add_argument("--base-url", default="http://fritz.box", help="Fritz!Box base URL")
    parser.add_argument("--username", help="Fritz!Box username")
    parser.add_argument("--password", help="Fritz!Box password")
    parser.add_argument(
        "--stdout-format",
        default="jsonl",
        choices=["jsonl", "text"],
        help="Emit entries to stdout in this format",
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

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(message)s",
    )

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
