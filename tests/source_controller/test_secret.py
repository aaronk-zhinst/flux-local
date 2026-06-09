"""Tests for secret parsing utilities."""

import base64

import pytest
import yaml

from flux_local.manifest import Secret
from flux_local.source_controller.secret import get_git_auth_from_secret


def _make_secret(string_data: dict | None = None, data: dict | None = None) -> Secret:
    doc: dict = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "test-secret", "namespace": "flux-system"},
    }
    if string_data:
        doc["stringData"] = string_data
    if data:
        doc["data"] = data
    return Secret.parse_doc(doc, wipe_secrets=False)


def test_get_git_auth_from_secret_string_data() -> None:
    """Reads username and password from stringData."""
    secret = _make_secret(string_data={"username": "ci-token", "password": "secret"})
    auth = get_git_auth_from_secret(secret)
    assert auth is not None
    assert auth.username == "ci-token"
    assert auth.password == "secret"


def test_get_git_auth_from_secret_base64_data() -> None:
    """Reads base64-encoded username and password from data."""
    secret = _make_secret(data={
        "username": base64.b64encode(b"ci-token").decode(),
        "password": base64.b64encode(b"secret").decode(),
    })
    auth = get_git_auth_from_secret(secret)
    assert auth is not None
    assert auth.username == "ci-token"
    assert auth.password == "secret"


def test_get_git_auth_from_secret_missing_credentials() -> None:
    """Returns None when neither username nor password is present."""
    secret = _make_secret(string_data={"unrelated-key": "value"})
    auth = get_git_auth_from_secret(secret)
    assert auth is None


def test_get_git_auth_from_secret_missing_password() -> None:
    """Returns None when password is absent."""
    secret = _make_secret(string_data={"username": "ci-token"})
    auth = get_git_auth_from_secret(secret)
    assert auth is None


def test_get_git_auth_from_secret_special_chars_in_password() -> None:
    """Handles passwords containing URL-special characters without corruption."""
    tricky_password = "p@ss:w0rd%20&?=+/test"
    secret = _make_secret(string_data={"username": "user", "password": tricky_password})
    auth = get_git_auth_from_secret(secret)
    assert auth is not None
    assert auth.password == tricky_password


def test_get_git_auth_from_secret_at_sign_in_username() -> None:
    """Handles usernames containing @ (e.g. email-style service accounts)."""
    secret = _make_secret(string_data={"username": "user@domain.com", "password": "pass"})
    auth = get_git_auth_from_secret(secret)
    assert auth is not None
    assert auth.username == "user@domain.com"
