"""POSIX flock-based leader election.

Exactly one colony-chat-hermes process per machine runs the
notification poller / webhook receiver; the rest are followers. The
leader holds an exclusive ``flock`` on a well-known path; a follower
that fails to acquire the lock falls back to read-only operation (tool
calls still work, just the daemon loop is dormant).

The pattern matches the existing ``colony-agent-lock`` flock wrapper
that ColonistOne uses on the same host. Single source of truth: only
one process can ever be "the one polling for inbound DMs" at a time.
"""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import IO

_DEFAULT_LOCK_DIR = Path.home() / ".hermes" / "locks"
_DEFAULT_LOCK_NAME = "colony-chat-hermes.lock"


class LeaderLockError(Exception):
    """Base class for leader-lock errors."""


class LeaderLockBusy(LeaderLockError):
    """Raised when another process already holds the leader lock.

    The Hermes plugin SHOULD catch this and continue as a follower —
    tool calls still work; only the daemon-side polling is dormant
    until the leader exits and the lock becomes free.
    """


class LeaderLock:
    """Exclusive flock on ``<lock_dir>/<lock_name>``.

    Use as a context manager::

        try:
            with LeaderLock() as lock:
                # we are the leader; run the poller
                run_poller(lock)
        except LeaderLockBusy:
            # follower mode — skip the poller
            pass

    The lock is **advisory** in the POSIX sense: a process that ignores
    the convention can still write to the file. The colony-chat-hermes
    runtime is the only thing that holds this lock, so the convention
    holds in practice.

    The lock file persists across runs; only the lock state is volatile.
    Deleting the file while a process holds the lock leaks the file but
    does not break the lock (the kernel tracks the lock on the open fd,
    not the path).
    """

    def __init__(
        self,
        *,
        lock_path: str | Path | None = None,
        lock_dir: str | Path | None = None,
        lock_name: str | None = None,
    ) -> None:
        if lock_path is not None:
            self._lock_path = Path(lock_path)
        else:
            d = Path(lock_dir) if lock_dir is not None else _DEFAULT_LOCK_DIR
            n = lock_name if lock_name is not None else _DEFAULT_LOCK_NAME
            self._lock_path = d / n
        self._fh: IO[bytes] | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def held(self) -> bool:
        return self._fh is not None

    def acquire(self) -> None:
        """Try to acquire the lock. Raises :class:`LeaderLockBusy` if
        another process already holds it."""
        if self._fh is not None:
            return  # already held by this instance — idempotent

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Open in read/write so the leader can write its PID for
        # diagnostics, and so subsequent ``open`` calls don't truncate.
        # ``open`` without a context manager is deliberate — the file
        # handle must outlive this function; it's closed in
        # :meth:`release` when the lock is released.
        fh = open(self._lock_path, "a+b", buffering=0)  # noqa: SIM115
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            fh.close()
            raise LeaderLockBusy(
                f"Another process holds {self._lock_path} (errno {e.errno})"
            ) from e
        except OSError as e:
            fh.close()
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise LeaderLockBusy(
                    f"Another process holds {self._lock_path} (errno {e.errno})"
                ) from e
            raise

        self._fh = fh
        # Write our PID into the lock file as a diagnostic. Truncate
        # first so stale PIDs from previous leaders don't accumulate.
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n".encode())
            fh.flush()
        except OSError:
            # Diagnostic write failures don't invalidate the lock.
            pass

    def release(self) -> None:
        """Release the lock if held. Idempotent."""
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                self._fh.close()
            finally:
                self._fh = None

    def __enter__(self) -> LeaderLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
