"""Local SQLite user store with bcrypt passwords and JWT session tokens.

Designed for air-gapped environments: no external identity provider required.
On first initialisation, a default admin account is created automatically.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
from jose import JWTError, jwt

ALGORITHM = "HS256"
ROLES = ("admin", "viewer")


def _hash_password(password: str) -> str:
    """Hash a password using bcrypt and return the string hash."""
    # bcrypt.hashpw returns bytes in bcrypt 4.0+
    hashed_bytes = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed_bytes.decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    role: str
    created_at: float

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TokenPayload:
    user_id: int
    username: str
    role: str


class UserStore:
    """Thread-safe SQLite user management with bcrypt password hashing."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._jwt_secret = os.getenv("RAG_JWT_SECRET", "")
        self._jwt_expiry_hours = int(os.getenv("RAG_JWT_EXPIRY_HOURS", "24"))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_secrets (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        self._ensure_jwt_secret()
        self._ensure_default_admin()

    def _ensure_jwt_secret(self) -> None:
        """Generate and persist a JWT signing key if none exists."""
        if self._jwt_secret:
            return
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_secrets WHERE key = 'jwt_secret'"
            ).fetchone()
            if row:
                self._jwt_secret = str(row["value"])
            else:
                self._jwt_secret = secrets.token_urlsafe(64)
                connection.execute(
                    "INSERT INTO app_secrets(key, value) VALUES (?, ?)",
                    ("jwt_secret", self._jwt_secret),
                )

    def _ensure_default_admin(self) -> None:
        """Create a default admin account if no users exist."""
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
            if int(row["count"]) == 0:
                username = os.getenv("RAG_ADMIN_USERNAME", "admin")
                password = os.getenv("RAG_ADMIN_PASSWORD", "changeme")
                connection.execute(
                    """
                    INSERT INTO users(username, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username, _hash_password(password), "admin", time.time()),
                )

    # ── User CRUD ─────────────────────────────────────────────────────────

    def create_user(self, username: str, password: str, role: str = "viewer") -> UserRecord:
        if role not in ROLES:
            raise ValueError(f"Invalid role: {role}. Must be one of {ROLES}")
        if not username or not username.strip():
            raise ValueError("Username cannot be empty")
        if len(password) < 4:
            raise ValueError("Password must be at least 4 characters")
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users(username, password_hash, role, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (username.strip(), _hash_password(password), role, time.time()),
                )
                row = connection.execute(
                    "SELECT id, username, role, created_at FROM users WHERE username = ?",
                    (username.strip(),),
                ).fetchone()
                return UserRecord(**dict(row))
            except sqlite3.IntegrityError:
                raise ValueError(f"Username '{username}' already exists")

    def get_user_by_id(self, user_id: int) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, role, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return UserRecord(**dict(row)) if row else None

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, role, created_at FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        return UserRecord(**dict(row)) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, username, role, created_at FROM users ORDER BY id"
            ).fetchall()
        return [UserRecord(**dict(row)) for row in rows]

    def delete_user(self, user_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0

    def update_role(self, user_id: int, new_role: str) -> UserRecord | None:
        if new_role not in ROLES:
            raise ValueError(f"Invalid role: {new_role}. Must be one of {ROLES}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET role = ? WHERE id = ?", (new_role, user_id)
            )
            row = connection.execute(
                "SELECT id, username, role, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        return UserRecord(**dict(row)) if row else None

    def change_password(self, user_id: int, new_password: str) -> bool:
        if len(new_password) < 4:
            raise ValueError("Password must be at least 4 characters")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (_hash_password(new_password), user_id),
            )
        return cursor.rowcount > 0

    # ── Authentication ────────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> UserRecord | None:
        """Verify credentials and return the user record, or None on failure."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if row is None:
            # Run a dummy hash comparison to prevent timing attacks
            _verify_password("dummy", _hash_password("dummy"))
            return None
        if not _verify_password(password, str(row["password_hash"])):
            return None
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
            created_at=float(row["created_at"]),
        )

    # ── JWT Tokens ────────────────────────────────────────────────────────

    def create_token(self, user: UserRecord) -> str:
        expire = datetime.now(timezone.utc) + timedelta(hours=self._jwt_expiry_hours)
        payload = {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "exp": expire,
        }
        return jwt.encode(payload, self._jwt_secret, algorithm=ALGORITHM)

    def verify_token(self, token: str) -> TokenPayload | None:
        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=[ALGORITHM])
            user_id = int(payload.get("sub", 0))
            username = str(payload.get("username", ""))
            role = str(payload.get("role", ""))
            if not user_id or not username or role not in ROLES:
                return None
            return TokenPayload(user_id=user_id, username=username, role=role)
        except (JWTError, ValueError, KeyError):
            return None

    def user_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"])
