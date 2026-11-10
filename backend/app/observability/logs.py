"""Structured logging (UX-05): one JSON object per line with the active trace and span ids, so logs and traces of
the same request/execution can be joined. `LOG_FORMAT=text` keeps the human-readable format for local debugging."""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

from app.security.crypto import redact

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(), "level": record.levelname,
               "service": self.service, "logger": record.name, "message": redact(record.getMessage())}
        try:
            from opentelemetry import trace
            ctx = trace.get_current_span().get_span_context()
            if ctx.is_valid:
                out["trace_id"], out["span_id"] = f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}"
        except Exception:  # noqa: BLE001, S110 — logging must never fail
            pass
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                out[k] = v if isinstance(v, int | float | bool | type(None)) else redact(str(v))
        if record.exc_info:
            out["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(service: str) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "json").lower() == "text":
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    else:
        h.setFormatter(JsonFormatter(service))
    root.addHandler(h)
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
