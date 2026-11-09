"""Persistent local scheduling. Outbound communications remain simulated.

Run independently with `python -m smartsehat_workspace.consultation.scheduler`; the
Streamlit process also starts one lightweight loop. Jobs survive restarts.
"""
from __future__ import annotations
import os
from pathlib import Path
import threading

_lock = threading.Lock()
_runners: dict[str, tuple[threading.Thread, threading.Event]] = {}


def start(path: str, interval: float = 10) -> None:
    if os.environ.get("CONSULTATION_DISABLE_SCHEDULER") == "1":
        return
    with _lock:
        existing = _runners.get(path)
        if existing and existing[0].is_alive():
            return
        stop = threading.Event()
        def loop():
            while not stop.wait(interval):
                if not Path(path).exists():
                    continue
                try:
                    from .store import Store
                    Store(path).tick()
                except Exception:
                    # A retryable database/model issue must not erase the pending job.
                    # Job exceptions are recorded by tick without patient content in logs.
                    continue
        thread = threading.Thread(target=loop, name="smartsehat-consultation-scheduler", daemon=True)
        _runners[path] = (thread, stop)
        thread.start()


if __name__ == "__main__":
    from .store import Store
    service = Store()
    print("Consultation local scheduler running; outbound channels are simulated.")
    try:
        while True:
            service.tick()
            threading.Event().wait(10)
    except KeyboardInterrupt:
        pass
