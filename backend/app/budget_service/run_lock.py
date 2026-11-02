"""Per-user ownership shared by CLI processes and the API, with fenced goal writes.

A unique database row is claimed before work starts. Heartbeats keep long model
calls alive; a dead process's claim expires. The owner token prevents an old
process from releasing or committing under a replacement owner's claim.
"""
import secrets
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError

from app.db.models import UserRunClaim

LEASE = timedelta(minutes=5)
HEARTBEAT_SECONDS = 30


class RunOwnershipLost(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc)


class UserRunLock:
    def __init__(self, user_id):
        self.user_id = user_id
        self._local = threading.Lock()
        self._factory = None
        self._owner = None
        self._stop = None
        self._thread = None

    def acquire(self, blocking=True, *, session_factory=None):
        if not self._local.acquire(blocking=blocking):
            return False
        if session_factory is None:  # compatibility for local callers/tests that only reserve the process lock
            return True
        owner = secrets.token_hex(16)
        try:
            with session_factory() as s:
                s.execute(delete(UserRunClaim).where(UserRunClaim.user_id == self.user_id,
                                                     UserRunClaim.expires_at <= now()))
                s.add(UserRunClaim(user_id=self.user_id, owner=owner, expires_at=now() + LEASE))
                s.commit()
        except IntegrityError:
            self._local.release()
            return False
        except BaseException:
            self._local.release()
            raise
        self._factory, self._owner = session_factory, owner
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return True

    def assert_owned(self, session):
        if self._owner is None:
            return
        renewed = session.execute(update(UserRunClaim).where(
            UserRunClaim.user_id == self.user_id, UserRunClaim.owner == self._owner,
            UserRunClaim.expires_at > now()).values(expires_at=now() + LEASE))
        if renewed.rowcount != 1:
            raise RunOwnershipLost('Run ownership expired or changed.')

    def _heartbeat(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                with self._factory() as s:
                    self.assert_owned(s)
                    s.commit()
            except Exception:
                # No raw DB errors in logs. A fenced write fails closed if renewal was lost.
                return

    def locked(self):
        return self._local.locked()

    def release(self):
        try:
            if self._stop is not None:
                self._stop.set()
                self._thread.join()
            if self._owner is not None:
                with self._factory() as s:
                    s.execute(delete(UserRunClaim).where(UserRunClaim.user_id == self.user_id,
                                                         UserRunClaim.owner == self._owner))
                    s.commit()
        finally:
            self._factory = self._owner = self._stop = self._thread = None
            self._local.release()
