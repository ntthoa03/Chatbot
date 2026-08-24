"""Ghi trace JSON Lines có che dữ liệu nhạy cảm."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
import warnings
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACE_PATH = PROJECT_ROOT / "outputs" / "traces.jsonl"
TRACE_SCHEMA_VERSION = "schema.v2"
_WRITE_LOCK = threading.Lock()


class TraceTimer:
    """Measure named pipeline stages with one monotonic clock."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._steps: dict[str, float] = {}

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = max(0.0, (time.perf_counter() - started) * 1000)
            self._steps[name] = round(self._steps.get(name, 0.0) + elapsed, 3)

    def snapshot(self, expected_steps: tuple[str, ...] = ()) -> dict[str, float]:
        timings = {name: self._steps.get(name, 0.0) for name in expected_steps}
        timings.update(self._steps)
        timings["total_ms"] = round(
            max(0.0, (time.perf_counter() - self._started) * 1000),
            3,
        )
        return timings


def new_trace_id() -> str:
    return str(uuid.uuid4())


def _redact_text(text: str) -> str:
    text = re.sub(
        r"(?i)\b(api[_ ]?key|secret[_ ]?key|access[_ ]?token)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"((?i:\bOTP\b)\D{0,12})\d{4,8}\b", r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[REDACTED]", text)
    text = re.sub(
        r"(?<!\w)(?:0|\+84)[\s.-]?(?:\d[\s.-]?){8,10}\b",
        "[REDACTED]",
        text,
    )
    return re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[REDACTED]",
        text,
        flags=re.I,
    )


def _sanitize(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def redact_sensitive_data(value: Any) -> Any:
    """Return a JSON-safe copy with the same PII/secret masking used by traces."""

    return _sanitize(value)


def trace_path() -> Path:
    configured = os.getenv("AI_CORE_TRACE_PATH")
    if not configured:
        return DEFAULT_TRACE_PATH
    path = Path(configured)
    return path if path.is_absolute() else PROJECT_ROOT / path


def log_trace(trace: dict) -> None:
    """Append một record JSONL; lỗi I/O không được làm hỏng response cho khách."""

    record = _sanitize(dict(trace))
    record.setdefault("logged_at", datetime.now(UTC).isoformat())
    destination = trace_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _WRITE_LOCK, destination.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
    except OSError as exc:
        warnings.warn(f"Không thể ghi trace tại {destination}: {exc}", RuntimeWarning, stacklevel=2)


def find_trace(trace_id: str) -> dict[str, Any] | None:
    """Return the newest trace record for an id, for internal eval diagnostics."""

    if not trace_id:
        return None
    destination = trace_path()
    try:
        with _WRITE_LOCK:
            lines = destination.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("trace_id") == trace_id:
            return record
    return None
