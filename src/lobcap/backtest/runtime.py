"""Host-wide execution exclusion and cooperative interruption evidence."""
import os
from pathlib import Path
import tempfile
import threading

from lobcap.backtest.types import jsonable


class RunLock:
    """OS-owned lock; automatically released by process death, never a stale PID file."""
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("LOBCAP_RUN_LOCK", Path(tempfile.gettempdir()) / "lobcap-backtest-worker.lock"))
        self._guard = threading.Lock()
        self._handle = None

    def acquire(self, blocking=True):
        if not self._guard.acquire(blocking=blocking): return False
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0: handle.write(b"0"); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self._handle = handle
            return True
        except OSError:
            if handle is not None: handle.close()
            self._guard.release()
            return False

    def release(self):
        if self._handle is None: raise RuntimeError("Run lock is not held")
        try:
            if os.name == "nt":
                import msvcrt
                self._handle.seek(0); msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        finally:
            self._handle = None
            self._guard.release()


class SimulationCanceled(RuntimeError):
    output_dir = None


def interrupt_if_requested(cancel_check, sink, time_ns, portfolio, execution):
    if cancel_check is None or not cancel_check(): return
    sink.write("interrupted", {"status": "CANCELED", "time_ns": time_ns,
        "cash": portfolio.cash if portfolio else None,
        "inventory": portfolio.inventory if portfolio else None,
        "orders": [jsonable(order.record()) for order in execution.orders.values()],
        "reason": "user_cancel", "full_window": False, "terminal_liquidation": False})
    raise SimulationCanceled("Backtest canceled at a timestamp boundary")
