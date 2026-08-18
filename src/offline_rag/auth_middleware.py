"""FastAPI dependency injectors for JWT authentication and role-based access."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from offline_rag.auth import TokenPayload, UserStore

# Module-level reference set by server_fastapi.py at startup
_user_store: UserStore | None = None

bearer_scheme = HTTPBearer(auto_error=False)


def set_user_store(store: UserStore) -> None:
    """Called once at server startup to wire the store into the dependency chain."""
    global _user_store
    _user_store = store


def _get_store() -> UserStore:
    if _user_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth subsystem not initialised",
        )
    return _user_store


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> TokenPayload:
    """Extract and validate the JWT from the Authorization header.

    Returns the decoded token payload or raises 401.
    """
    store = _get_store()
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = store.verify_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def get_ws_user(token: str = Query(...)) -> TokenPayload:
    """Extract and validate the JWT from a WebSocket query parameter.

    WebSocket connections cannot send custom headers, so we pass
    the token as ``?token=<jwt>`` in the connection URL.
    """
    store = _get_store()
    payload = store.verify_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return payload


def require_role(*allowed_roles: str):
    """Return a FastAPI dependency that enforces role membership."""

    def _checker(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(allowed_roles)}",
            )
        return user

    return _checker
