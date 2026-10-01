"""Optional progress callbacks keep the service independent of its CLI."""
from collections.abc import Callable
from datetime import datetime
import sys

ProgressCallback = Callable[[str], None]


def quiet_progress(message: str) -> None:
    pass


def console_progress(message: str) -> None:
    # Flush even when stdout is redirected: long analysis must never look silent.
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)
