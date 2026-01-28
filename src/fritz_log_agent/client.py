"""Fritz!Box login and log retrieval client."""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import requests


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    group: str
    entry_id: str
    message: str

    def to_text_line(self) -> str:
        date_str = self.timestamp.strftime("%d.%m.%y")
        time_str = self.timestamp.strftime("%H:%M:%S")
        return f"{date_str} {time_str} {self.group} {self.entry_id} {self.message}"

    def to_json(self) -> dict:
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
            "group": self.group,
            "id": self.entry_id,
            "msg": self.message,
        }


def _sanitize_password(password: str) -> str:
    return "".join(ch if ord(ch) <= 255 else "." for ch in password)


def _is_zero_sid(sid: str) -> bool:
    return bool(sid) and set(sid) == {"0"}


def _parse_login_xml(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"sid": "", "challenge": "", "blocktime": 0}

    sid = root.findtext("SID", default="")
    challenge = root.findtext("Challenge", default="")
    blocktime_text = root.findtext("BlockTime", default="0")
    try:
        blocktime = int(blocktime_text)
    except ValueError:
        blocktime = 0
    return {"sid": sid, "challenge": challenge, "blocktime": blocktime}


def _parse_timestamp(date_str: str, time_str: str) -> datetime:
    for fmt in ("%d.%m.%y %H:%M:%S", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    raise ValueError(f"Unrecognized date/time: {date_str} {time_str}")


class FritzClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.sid: str | None = None

    def login(self) -> str:
        login_url = f"{self.base_url}/login_sid.lua"
        resp = self.session.get(login_url, timeout=self.timeout)
        resp.raise_for_status()

        info = _parse_login_xml(resp.text)
        if info["blocktime"] > 0:
            time.sleep(info["blocktime"] + 1)

        if not _is_zero_sid(info["sid"]):
            self.sid = info["sid"]
            return self.sid

        if not info["challenge"]:
            raise RuntimeError("Missing login challenge from Fritz!Box")

        clean_password = _sanitize_password(self.password)
        challenge = info["challenge"]
        raw = f"{challenge}-{clean_password}".encode("utf-16le")
        md5_hex = hashlib.md5(raw).hexdigest()
        response = f"{challenge}-{md5_hex}"

        resp = self.session.get(
            login_url,
            params={"response": response, "username": self.username},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        info = _parse_login_xml(resp.text)
        if _is_zero_sid(info["sid"]):
            raise RuntimeError("Login failed (SID is zero)")
        self.sid = info["sid"]
        return self.sid

    def ensure_login(self) -> str:
        if not self.sid or _is_zero_sid(self.sid):
            return self.login()
        return self.sid

    def fetch_log(self) -> tuple[list[LogEntry], dict]:
        sid = self.ensure_login()
        url = f"{self.base_url}/data.lua"
        resp = self.session.post(
            url,
            data={"xhr": "1", "lang": "de", "page": "log", "sid": sid},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()

        if "data" not in payload or "log" not in payload.get("data", {}):
            raise RuntimeError("Unexpected response shape from Fritz!Box")

        entries = list(_entries_from_payload(payload["data"]["log"]))
        return entries, payload

    def fetch_log_with_retry(self) -> tuple[list[LogEntry], dict]:
        try:
            return self.fetch_log()
        except Exception:
            self.sid = None
            self.login()
            return self.fetch_log()


def _entries_from_payload(items: Iterable[dict]) -> Iterable[LogEntry]:
    for item in items:
        date_str = str(item.get("date", ""))
        time_str = str(item.get("time", ""))
        if not date_str or not time_str:
            continue
        timestamp = _parse_timestamp(date_str, time_str)
        group = str(item.get("group", ""))
        entry_id = str(item.get("id", ""))
        message = str(item.get("msg", ""))
        yield LogEntry(timestamp=timestamp, group=group, entry_id=entry_id, message=message)
