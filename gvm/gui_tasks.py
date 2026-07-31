"""Background-task plumbing for the GUI.

Tk is single-threaded: only the thread that created a widget may touch it. The
GUI therefore runs slow work (network calls, installs, extractions) on daemon
threads that communicate **only** through a queue, which the main thread drains
on a timer. Nothing here imports customtkinter, so the policy is testable
without a display.

The rules this module enforces:

* One heavy task at a time. Concurrent tasks would interleave their
  read-modify-write cycles on ``cache.toml`` and lose each other's changes.
* Workers never touch widgets. They ``post()`` strings (status updates) or
  tagged tuples (structured events); the main thread interprets them.
* Every task posts a completion sentinel even when it raised, so the busy flag
  can never be left stuck on after an error.
"""

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Posted by _run() when a task finishes, successfully or not.
TASK_DONE = None


class TaskRunner:
    """Runs one background task at a time and funnels results to a queue."""

    def __init__(self) -> None:
        self.queue: queue.Queue[Any] = queue.Queue()
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def post(self, message: Any) -> None:
        """Queue a message for the main thread. Safe from any thread."""
        self.queue.put(message)

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> bool:
        """Start *fn* on a daemon thread.

        Returns False (and starts nothing) if a task is already running, so the
        caller can tell the user rather than silently dropping the request.
        """
        if self._busy:
            return False
        self._busy = True
        threading.Thread(
            target=self._wrap, args=(fn, *args), kwargs=kwargs, daemon=True
        ).start()
        return True

    def _wrap(self, fn: Callable[..., Any], *args, **kwargs) -> None:
        """Worker-thread entry point. Never raises into the thread."""
        try:
            fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - surfaced to the UI, not swallowed
            logger.debug("Background task failed", exc_info=True)
            self.post(f"Error: {e}")
        finally:
            # Always post the sentinel: without it a failed task would leave the
            # UI permanently "busy" and refusing every subsequent action.
            self.post(TASK_DONE)

    def drain(self) -> list[Any]:
        """Pop every queued message. Main thread only."""
        messages: list[Any] = []
        try:
            while True:
                messages.append(self.queue.get_nowait())
        except queue.Empty:
            pass
        return messages

    def mark_idle(self) -> None:
        """Clear the busy flag. Called by the main thread on the sentinel."""
        self._busy = False


def marshal(widget, callback: Callable[[], None]) -> None:
    """Run *callback* on the Tk main thread, if the widget still exists.

    Worker threads use this to request a UI update. The existence check matters:
    a fetch that finishes after the window has been destroyed (the self-update
    restart path does exactly that) would otherwise raise inside Tk.
    """
    def _guarded() -> None:
        try:
            if widget.winfo_exists():
                callback()
        except Exception:  # pragma: no cover - teardown races
            pass

    try:
        widget.after(0, _guarded)
    except Exception:  # pragma: no cover - widget already gone
        pass


def reap(children: list) -> list:
    """Drop finished child processes from *children*.

    Ghidra is launched as a child so the GUI can stay open; without reaping,
    exited children linger as zombies for the GUI's lifetime.
    """
    return [c for c in children if c.poll() is None]


def parse_event(message: Any) -> tuple[str, Any | None]:
    """Classify a queued message into ``(kind, payload)``.

    Kinds: ``"done"`` (sentinel), ``"event"`` (tagged tuple), ``"status"``
    (plain string for the status bar).
    """
    if message is TASK_DONE:
        return ("done", None)
    if isinstance(message, tuple) and message and isinstance(message[0], str):
        return ("event", message)
    return ("status", message)
