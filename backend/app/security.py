import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


# Compared against when a username does not exist, so timing doesn't reveal valid usernames.
DUMMY_HASH = hash_password("not-a-real-password")


def _verify(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


async def verify_password(password: str, password_hash: str | None) -> bool:
    """bcrypt is CPU-bound, so run it off the event loop."""
    ok = await asyncio.to_thread(_verify, password, password_hash or DUMMY_HASH)
    return ok and password_hash is not None


def create_access_token(staff_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode({"sub": str(staff_id), "exp": exp}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
