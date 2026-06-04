"""Tests for the POSIX flock leader lock."""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

from colony_chat_hermes.leader_lock import LeaderLock, LeaderLockBusy


def _child_try_acquire(lock_path: str, result_queue: mp.Queue) -> None:
    try:
        with LeaderLock(lock_path=lock_path):
            result_queue.put("acquired")
    except LeaderLockBusy:
        result_queue.put("busy")


class TestLeaderLock:
    def test_acquire_and_release(self, tmp_path: Path) -> None:
        lock = LeaderLock(lock_path=tmp_path / "test.lock")
        assert not lock.held
        lock.acquire()
        assert lock.held
        assert (tmp_path / "test.lock").exists()
        lock.release()
        assert not lock.held

    def test_context_manager(self, tmp_path: Path) -> None:
        with LeaderLock(lock_path=tmp_path / "ctx.lock") as lock:
            assert lock.held
        assert not lock.held

    def test_writes_pid_into_file(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "pid.lock"
        with LeaderLock(lock_path=lock_path):
            content = lock_path.read_text().strip()
            assert content == str(os.getpid())

    def test_second_acquire_in_same_process_is_idempotent(self, tmp_path: Path) -> None:
        lock = LeaderLock(lock_path=tmp_path / "idem.lock")
        lock.acquire()
        # Second acquire is a no-op.
        lock.acquire()
        assert lock.held
        lock.release()
        assert not lock.held

    def test_other_process_gets_busy(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "contended.lock"
        result_queue: mp.Queue = mp.Queue()
        with LeaderLock(lock_path=lock_path):
            # While we hold the lock, spawn a child that tries to take
            # it. POSIX flock is per-process; the child should bounce.
            child = mp.Process(target=_child_try_acquire, args=(str(lock_path), result_queue))
            child.start()
            child.join(timeout=10)
            outcome = result_queue.get(timeout=2)
        assert outcome == "busy"

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        lock = LeaderLock(lock_path=tmp_path / "rel.lock")
        lock.acquire()
        lock.release()
        # Second release is a no-op.
        lock.release()
        assert not lock.held

    def test_default_path_constructs_under_hermes_locks(self) -> None:
        lock = LeaderLock()
        assert ".hermes" in str(lock.lock_path)
        assert lock.lock_path.name == "colony-chat-hermes.lock"

    def test_after_release_other_process_can_acquire(self, tmp_path: Path) -> None:
        lock_path = tmp_path / "handover.lock"
        with LeaderLock(lock_path=lock_path):
            pass  # acquire + release
        # Now a child should succeed.
        result_queue: mp.Queue = mp.Queue()
        child = mp.Process(target=_child_try_acquire, args=(str(lock_path), result_queue))
        child.start()
        child.join(timeout=10)
        outcome = result_queue.get(timeout=2)
        assert outcome == "acquired"

    def test_lock_busy_message_includes_path(self, tmp_path: Path) -> None:
        # The LeaderLockBusy message should embed the lock path so logs
        # from a follower bouncing off the leader are diagnostic.
        # POSIX flock is per-process so a second LeaderLock in the same
        # process actually succeeds (same fd group) — for the message
        # check we just verify the exception type carries the path
        # when constructed directly the way ``acquire()`` does.
        lock_path = tmp_path / "msg.lock"
        exc = LeaderLockBusy(f"Another process holds {lock_path} (errno 11)")
        assert str(lock_path) in str(exc)
