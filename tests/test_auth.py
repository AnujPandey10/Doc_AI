"""Tests for the authentication module."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from offline_rag.auth import UserStore


@pytest.fixture()
def store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "test_users.sqlite3")


class TestUserStore:
    def test_default_admin_created(self, store: UserStore):
        """A default admin is created on first initialization."""
        users = store.list_users()
        assert len(users) == 1
        assert users[0].username == "admin"
        assert users[0].role == "admin"

    def test_authenticate_default_admin(self, store: UserStore):
        """Default admin can authenticate with default credentials."""
        user = store.authenticate("admin", "changeme")
        assert user is not None
        assert user.role == "admin"

    def test_authenticate_wrong_password(self, store: UserStore):
        user = store.authenticate("admin", "wrongpassword")
        assert user is None

    def test_authenticate_nonexistent_user(self, store: UserStore):
        user = store.authenticate("nonexistent", "password")
        assert user is None

    def test_create_user(self, store: UserStore):
        user = store.create_user("testuser", "testpass", "viewer")
        assert user.username == "testuser"
        assert user.role == "viewer"
        assert user.id > 0

    def test_create_duplicate_user_raises(self, store: UserStore):
        store.create_user("dupuser", "pass1234", "viewer")
        with pytest.raises(ValueError, match="already exists"):
            store.create_user("dupuser", "pass5678", "viewer")

    def test_create_user_invalid_role(self, store: UserStore):
        with pytest.raises(ValueError, match="Invalid role"):
            store.create_user("baduser", "password", "superadmin")

    def test_create_user_short_password(self, store: UserStore):
        with pytest.raises(ValueError, match="at least 4"):
            store.create_user("shortpw", "ab", "viewer")

    def test_create_user_empty_username(self, store: UserStore):
        with pytest.raises(ValueError, match="cannot be empty"):
            store.create_user("", "password", "viewer")

    def test_delete_user(self, store: UserStore):
        user = store.create_user("todelete", "password", "viewer")
        assert store.delete_user(user.id)
        assert store.get_user_by_id(user.id) is None

    def test_delete_nonexistent_user(self, store: UserStore):
        assert not store.delete_user(99999)

    def test_update_role(self, store: UserStore):
        user = store.create_user("roleuser", "password", "viewer")
        updated = store.update_role(user.id, "admin")
        assert updated is not None
        assert updated.role == "admin"

    def test_update_role_invalid(self, store: UserStore):
        user = store.create_user("badrole", "password", "viewer")
        with pytest.raises(ValueError, match="Invalid role"):
            store.update_role(user.id, "superadmin")

    def test_change_password(self, store: UserStore):
        user = store.create_user("pwchange", "oldpass1", "viewer")
        assert store.change_password(user.id, "newpass1")
        # Old password should fail
        assert store.authenticate("pwchange", "oldpass1") is None
        # New password should work
        assert store.authenticate("pwchange", "newpass1") is not None

    def test_change_password_too_short(self, store: UserStore):
        user = store.create_user("shortpw2", "password", "viewer")
        with pytest.raises(ValueError, match="at least 4"):
            store.change_password(user.id, "ab")

    def test_list_users(self, store: UserStore):
        store.create_user("user1", "pass1234", "viewer")
        store.create_user("user2", "pass5678", "admin")
        users = store.list_users()
        assert len(users) == 3  # admin + user1 + user2
        usernames = {u.username for u in users}
        assert "admin" in usernames
        assert "user1" in usernames
        assert "user2" in usernames

    def test_user_count(self, store: UserStore):
        assert store.user_count() == 1  # default admin
        store.create_user("countuser", "password", "viewer")
        assert store.user_count() == 2


class TestJWT:
    def test_create_and_verify_token(self, store: UserStore):
        user = store.authenticate("admin", "changeme")
        assert user is not None
        token = store.create_token(user)
        assert isinstance(token, str)
        assert len(token) > 0

        payload = store.verify_token(token)
        assert payload is not None
        assert payload.user_id == user.id
        assert payload.username == user.username
        assert payload.role == user.role

    def test_verify_invalid_token(self, store: UserStore):
        payload = store.verify_token("invalid.token.here")
        assert payload is None

    def test_verify_empty_token(self, store: UserStore):
        payload = store.verify_token("")
        assert payload is None

    def test_token_contains_correct_role(self, store: UserStore):
        viewer = store.create_user("viewer1", "viewpass", "viewer")
        token = store.create_token(viewer)
        payload = store.verify_token(token)
        assert payload is not None
        assert payload.role == "viewer"

    def test_user_as_dict(self, store: UserStore):
        user = store.create_user("dictuser", "password", "viewer")
        d = user.as_dict()
        assert d["username"] == "dictuser"
        assert d["role"] == "viewer"
        assert "id" in d
        assert "created_at" in d
