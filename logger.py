"""
logger.py

Central loggningskonfiguration för hela appen.

Exponerar:
- get_logger(name)        — returnerar en konfigurerad logger
- get_buffer()            — snapshot av senaste loggraderna (för /logs)
- subscribe()/unsubscribe — för SSE-strömning till UI:t
- new_run(label)          — markerar början på en ny scan-körning i loggen

Loggar går samtidigt till:
1. stdout med färger och tidsstämplar (synligt i terminalen där uvicorn körs)
2. logs/scan.log (fil för historik)
3. En in-memory ring-buffer (max 2000 rader) så UI:t kan visa loggen live
"""

import asyncio
import logging
import sys
import threading
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_RING_MAX = 2000
_RING_BUFFER: deque = deque(maxlen=_RING_MAX)
_subscribers: list = []
_subs_lock = threading.Lock()

# Aktivt run_id sätts av new_run() och taggas på alla loggrader.
_current_run = {"id": "boot", "label": "boot"}


class _ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    DIM = "\033[2m"
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(7)
        name = record.name.replace("a11y.", "").ljust(10)[:10]
        msg = record.getMessage()
        line = f"{self.DIM}{ts}{self.RESET} {color}{level}{self.RESET} {self.DIM}{name}{self.RESET} {msg}"
        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return line


class _RingBufferHandler(logging.Handler):
    """Sparar varje logg-rad i ring-bufferten + pushar till SSE-prenumeranter."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
                "level": record.levelname,
                "logger": record.name.replace("a11y.", ""),
                "message": record.getMessage(),
                "run_id": _current_run["id"],
                "run_label": _current_run["label"],
            }
            if record.exc_info:
                entry["exception"] = "".join(traceback.format_exception(*record.exc_info))
            _RING_BUFFER.append(entry)

            # Distribuera till SSE-prenumeranter.
            with _subs_lock:
                subs = list(_subscribers)
            for sub in subs:
                loop = sub["loop"]
                queue: asyncio.Queue = sub["queue"]
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, entry)
                except Exception:
                    pass
        except Exception:
            # Aldrig låta logghanteraren krascha kallaren.
            pass


_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return

    root = logging.getLogger("a11y")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    # 1. Konsol med färger
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(_ColorFormatter())
    console.setLevel(logging.DEBUG)
    root.addHandler(console)

    # 2. Fil utan färgkoder
    file_handler = logging.FileHandler(LOG_DIR / "scan.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        fmt="[%(asctime)s.%(msecs)03d] %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # 3. Ring-buffer för UI / SSE
    ring = _RingBufferHandler()
    ring.setLevel(logging.DEBUG)
    root.addHandler(ring)

    # Tysta tredjeparts-loggers vi inte vill spamma
    for noisy in ("httpx", "httpcore", "openai._base_client", "anthropic._base_client",
                  "chromadb", "asyncio", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _initialized = True


def get_logger(name: str) -> logging.Logger:
    """Returnerar en logger under 'a11y.<name>'-hierarkin."""
    _ensure_init()
    short = name
    if short == "__main__":
        short = "main"
    if short.startswith("a11y."):
        short = short[len("a11y."):]
    return logging.getLogger(f"a11y.{short}")


def new_run(label: str) -> str:
    """Markerar början på en ny scan-körning. Returnerar run_id."""
    _ensure_init()
    run_id = uuid.uuid4().hex[:8]
    _current_run["id"] = run_id
    _current_run["label"] = label
    log = get_logger("run")
    log.info("─" * 60)
    log.info("NY KÖRNING [%s] %s", run_id, label)
    log.info("─" * 60)
    return run_id


def get_buffer(limit: int | None = None, since_run: str | None = None) -> list:
    """Returnerar en kopia av ring-bufferten."""
    items = list(_RING_BUFFER)
    if since_run:
        items = [e for e in items if e.get("run_id") == since_run]
    if limit:
        items = items[-limit:]
    return items


def subscribe(loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
    """Registrera en kö som tar emot alla framtida loggrader."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    with _subs_lock:
        _subscribers.append({"loop": loop, "queue": queue})
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    with _subs_lock:
        _subscribers[:] = [s for s in _subscribers if s["queue"] is not queue]
