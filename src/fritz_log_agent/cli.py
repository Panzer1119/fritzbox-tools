"""CLI for downloading Fritz!Box logs and running in agent mode."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from getpass import getpass
from pathlib import Path
from typing import Iterable

from src.fritz_log_agent.client import FritzClient, LogEntry

#from .client import FritzClient, LogEntry



LOGGER = logging.getLogger("fritz_log_agent")


def _read_credentials(
    username: str | None,
    password: str | None,
    credentials_file: Path | None,
) -> tuple[str, str]:
    if username and password:
        return username, password

    if credentials_file and credentials_file.exists():
        content = credentials_file.read_text(encoding="utf-8").strip()
        if content:
            parts = content.split()
            if len(parts) >= 2:
                username = username or parts[0]
                password = password or parts[1]

    if not username:
        username = input("Fritz!Box username: ").strip()
    if not password:
        password = getpass("Fritz!Box password: ").strip()

    if not username or not password:
        raise RuntimeError("Missing username or password")
    return username, password


def _ensure_dirs(output_dir: Path) -> tuple[Path, Path]:
    json_dir = output_dir / "json"
    txt_dir = output_dir / "txt"
    json_dir.mkdir(parents=True, exist_ok=True)
    txt_dir.mkdir(parents=True, exist_ok=True)
    return json_dir, txt_dir


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, entries: Iterable[LogEntry]) -> None:
    lines = [entry.to_text_line() for entry in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pick_combined_file(txt_dir: Path, max_size: int, timestamp: str) -> Path:
    candidates = sorted(txt_dir.glob("log-combined*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return txt_dir / f"log-combined-{timestamp}.txt"

    current = candidates[0]
    if current.stat().st_size > max_size:
        return txt_dir / f"log-combined-{timestamp}.txt"
    return current


def _previous_json_timestamp(json_dir: Path) -> datetime:
    json_files = sorted(json_dir.glob("log-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(json_files) < 2:
        return datetime.fromtimestamp(0)

    previous = json_files[1]
    data = json.loads(previous.read_text(encoding="utf-8"))
    log_items = data.get("data", {}).get("log", [])
    if not log_items:
        return datetime.fromtimestamp(0)

    item = log_items[0]
    date_str = str(item.get("date", ""))
    time_str = str(item.get("time", ""))
    if not date_str or not time_str:
        return datetime.fromtimestamp(0)

    return _parse_timestamp(date_str, time_str)


def _parse_timestamp(date_str: str, time_str: str) -> datetime:
    for fmt in ("%d.%m.%y %H:%M:%S", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date/time: {date_str} {time_str}")


def _append_combined(
    combined_path: Path, entries: Iterable[LogEntry], last_timestamp: datetime
) -> datetime:
    entries_sorted = sorted(entries, key=lambda entry: entry.timestamp)
    new_entries = [entry for entry in entries_sorted if entry.timestamp > last_timestamp]
    if not new_entries:
        return last_timestamp

    lines = [entry.to_text_line() for entry in new_entries]
    with combined_path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return new_entries[-1].timestamp


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"last_timestamp": "", "last_signatures": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"last_timestamp": "", "last_signatures": []}


def _save_state(path: Path, last_timestamp: datetime, signatures: set[str]) -> None:
    payload = {
        "last_timestamp": last_timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_signatures": sorted(signatures),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _entry_signature(entry: LogEntry) -> str:
    return f"{entry.group}|{entry.entry_id}|{entry.message}"


def _emit_stdout(entries: Iterable[LogEntry], fmt: str) -> None:
    if fmt == "text":
        for entry in entries:
            print(entry.to_text_line())
    else:
        for entry in entries:
            print(json.dumps(entry.to_json(), ensure_ascii=True))


def _run_once(client: FritzClient, args: argparse.Namespace) -> int:
    entries, payload = client.fetch_log_with_retry()
    entries_sorted = sorted(entries, key=lambda entry: entry.timestamp)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = Path(args.output_dir).resolve()

    if args.output_format in {"json", "both", "text"}:
        json_dir, txt_dir = _ensure_dirs(output_dir)
    else:
        json_dir = txt_dir = None

    if args.output_format in {"json", "both"}:
        json_path = json_dir / f"log-{timestamp}.json"
        _write_json(json_path, payload)
        LOGGER.info("Wrote JSON log to %s", json_path)

    if args.output_format in {"text", "both"}:
        txt_path = txt_dir / f"log-{timestamp}.txt"
        _write_text(txt_path, entries_sorted)
        LOGGER.info("Wrote text log to %s", txt_path)

    if args.output_format in {"text", "both"} and args.combine:
        combined_path = _pick_combined_file(txt_dir, args.combined_max_size, timestamp)
        last_timestamp = _previous_json_timestamp(json_dir)
        last_timestamp = _append_combined(combined_path, entries_sorted, last_timestamp)
        LOGGER.info("Appended combined log to %s", combined_path)
        LOGGER.debug("Combined log last timestamp %s", last_timestamp)

    if args.stdout_format:
        _emit_stdout(entries_sorted, args.stdout_format)

    return 0


def _run_agent(client: FritzClient, args: argparse.Namespace) -> int:
    state_path = Path(args.state_file).resolve()
    state = _load_state(state_path)

    last_timestamp = datetime.fromtimestamp(0)
    if state.get("last_timestamp"):
        last_timestamp = datetime.strptime(state["last_timestamp"], "%Y-%m-%dT%H:%M:%S")

    last_signatures = set(state.get("last_signatures", []))

    while True:
        try:
            entries, payload = client.fetch_log_with_retry()
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
            _emit_stdout(new_entries, args.stdout_format or "jsonl")

        if args.output_format in {"json", "both", "text"}:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            output_dir = Path(args.output_dir).resolve()
            json_dir, txt_dir = _ensure_dirs(output_dir)

            if args.output_format in {"json", "both"}:
                json_path = json_dir / f"log-{timestamp}.json"
                _write_json(json_path, payload)

            if args.output_format in {"text", "both"}:
                txt_path = txt_dir / f"log-{timestamp}.txt"
                _write_text(txt_path, entries_sorted)

        _save_state(state_path, last_timestamp, last_signatures)
        time.sleep(args.interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download Fritz!Box logs and run in agent mode.")
    parser.add_argument("--base-url", default="http://fritz.box", help="Fritz!Box base URL")
    parser.add_argument("--username", help="Fritz!Box username")
    parser.add_argument("--password", help="Fritz!Box password")
    parser.add_argument(
        "--credentials-file",
        help="Path to credentials file (username password)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd()),
        help="Output directory for json/txt files",
    )
    parser.add_argument(
        "--output-format",
        default="both",
        choices=["json", "text", "both", "none"],
        help="Which file outputs to write",
    )
    parser.add_argument(
        "--stdout-format",
        choices=["jsonl", "text"],
        help="Also emit entries to stdout in this format",
    )
    parser.add_argument("--combine", action="store_true", help="Append to combined log file")
    parser.add_argument(
        "--combined-max-size",
        type=int,
        default=5 * 1024 * 1024,
        help="Max combined log size before rollover (bytes)",
    )
    parser.add_argument("--agent", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Polling interval in seconds for agent mode",
    )
    parser.add_argument(
        "--state-file",
        default=str(Path.cwd() / "agent_state.json"),
        help="State file to keep last seen timestamp",
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

    credentials_file = Path(args.credentials_file).resolve() if args.credentials_file else None
    username, password = _read_credentials(args.username, args.password, credentials_file)

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
