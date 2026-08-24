import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from app.config import settings


_BCRYPT_MAX_BYTES = 72  # bcrypt algoritma siniri; fazlasi sessizce kesilir


def _bcrypt_input(password: str) -> bytes:
    # Schemas 128 karaktere izin veriyor ama bcrypt yalnizca ilk 72 byte'i kullanir.
    # Hash ve verify ayni sekilde keserek tutarli davranisi garanti eder.
    return password.encode()[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_bcrypt_input(password), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_bcrypt_input(plain), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode(
        {"sub": str(user_id), "exp": expire, "iat": now, "jti": str(uuid.uuid4())},
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def create_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str) -> UUID | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        return UUID(payload["sub"])
    except (JWTError, ValueError, KeyError):
        return None
