"""Session JWT creation and verification (TRD section 5)."""

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import Settings
from app.core.errors import unauthorized


def create_session_token(settings: Settings, user_id: str) -> tuple[str, datetime]:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    token = jwt.encode(
        {"sub": user_id, "exp": expires, "iat": datetime.now(timezone.utc)},
        settings.session_jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return token, expires


def verify_session_token(settings: Settings, token: str) -> str:
    """Return `sub` (the user ID). Signature and expiry are checked every call."""
    try:
        claims = jwt.decode(
            token,
            settings.session_jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
    except jwt.PyJWTError:
        raise unauthorized("Invalid or expired session.") from None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise unauthorized("Invalid or expired session.")
    return sub
