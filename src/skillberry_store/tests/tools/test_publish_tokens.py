# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Scoped publish tokens — docs/design/npx.md §4.3.3 / §4.3.7."""

from __future__ import annotations

import json
import logging

import pytest

from skillberry_store.tools import publish_tokens as pt

SECRET = b"a-test-secret"


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def test_token_is_43_urlsafe_base64_chars():
    """§5.10 #5: base64url of the full digest, ``=`` stripped."""
    token = pt.publish_token(SECRET, "alice", pt.skill_scope("pdf-forms"))
    assert len(token) == pt.TOKEN_LENGTH == 43
    assert "=" not in token
    assert all(c.isalnum() or c in "-_" for c in token)


def test_token_is_stable_for_the_same_inputs():
    """The URL lives in a lockfile and is replayed by every `npx skills update`."""
    first = pt.publish_token(SECRET, "alice", "skill:a")
    second = pt.publish_token(SECRET, "alice", "skill:a")
    assert first == second


@pytest.mark.parametrize(
    "tenant,scope",
    [
        ("bob", "skill:a"),
        ("alice", "skill:b"),
        ("alice", "ns:a"),
        ("alice", "*"),
    ],
)
def test_token_differs_per_tenant_and_per_scope(tenant, scope):
    base = pt.publish_token(SECRET, "alice", "skill:a")
    assert pt.publish_token(SECRET, tenant, scope) != base


def test_rotating_the_secret_invalidates_every_token():
    """The global revoke the derived scheme has — a control, not a hazard."""
    before = pt.publish_token(SECRET, "alice", "skill:a")
    assert pt.publish_token(b"rotated", "alice", "skill:a") != before


def test_token_does_not_contain_the_tenant_id():
    """Otherwise the username reaches a shell history and Vercel's telemetry."""
    token = pt.publish_token(SECRET, "alice", "skill:pdf-forms")
    assert "alice" not in token
    assert "pdf-forms" not in token


def test_scope_helpers():
    assert pt.skill_scope("pdf-forms") == "skill:pdf-forms"
    assert pt.namespace_scope("data-eng") == "ns:data-eng"
    assert pt.SCOPE_ALL == "*"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_resolve_token_recovers_the_tenant_and_scope():
    token = pt.publish_token(SECRET, "bob", "skill:pdf-forms")
    got = pt.resolve_token(
        token, SECRET, ["alice", "bob"], ["skill:pdf-forms", "skill:other"]
    )
    assert got == ("bob", "skill:pdf-forms")


def test_resolve_token_rejects_a_scope_outside_the_candidate_set():
    """Skill A's token must not open skill B's URL (§4.3.7, test #21)."""
    token = pt.publish_token(SECRET, "bob", "skill:a")
    assert pt.resolve_token(token, SECRET, ["bob"], ["skill:b"]) is None


def test_resolve_token_rejects_a_tenant_outside_the_candidate_set():
    token = pt.publish_token(SECRET, "carol", "skill:a")
    assert pt.resolve_token(token, SECRET, ["alice", "bob"], ["skill:a"]) is None


def test_resolve_token_rejects_a_rotated_secret():
    token = pt.publish_token(SECRET, "bob", "skill:a")
    assert pt.resolve_token(token, b"rotated", ["bob"], ["skill:a"]) is None


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 43])
def test_resolve_token_rejects_junk(token):
    assert pt.resolve_token(token, SECRET, ["bob"], ["skill:a"]) is None


def test_resolve_token_with_no_candidates_is_none():
    token = pt.publish_token(SECRET, "bob", "skill:a")
    assert pt.resolve_token(token, SECRET, [], ["skill:a"]) is None
    assert pt.resolve_token(token, SECRET, ["bob"], []) is None


def test_resolve_token_handles_multiple_scopes_per_tenant():
    tenants = ["alice", "bob"]
    scopes = ["skill:a", "ns:data", "*"]
    for tenant in tenants:
        for scope in scopes:
            token = pt.publish_token(SECRET, tenant, scope)
            assert pt.resolve_token(token, SECRET, tenants, scopes) == (tenant, scope)


# --------------------------------------------------------------------------- #
# candidate_tenants (§5.10 #4)
# --------------------------------------------------------------------------- #
def test_candidate_tenants_reads_configured_users_only():
    from skillberry_store.access_control.config import AccessControlConfig, User

    cfg = AccessControlConfig(
        mode="standalone",
        users=[
            User(username="skillberry", tenant_id="skillberry", password_hash="x"),
            User(
                username="skillberry-admin",
                tenant_id="skillberry-admin",
                password_hash="x",
            ),
        ],
        plugin_owner_tenant="plugin-user",
    )
    # `plugin-user` is a virtual subject with no users entry — excluded for free.
    assert pt.candidate_tenants(cfg) == ["skillberry", "skillberry-admin"]


def test_candidate_tenants_deduplicates():
    from skillberry_store.access_control.config import AccessControlConfig, User

    cfg = AccessControlConfig(
        users=[
            User(username="a", tenant_id="shared", password_hash="x"),
            User(username="b", tenant_id="shared", password_hash="x"),
        ]
    )
    assert pt.candidate_tenants(cfg) == ["shared"]


def test_candidate_tenants_of_none_is_empty():
    assert pt.candidate_tenants(None) == []


# --------------------------------------------------------------------------- #
# Secret loading and persistence
# --------------------------------------------------------------------------- #
def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(pt.SECRET_ENV_VAR, "from-env")
    (tmp_path / "s.json").write_text(json.dumps({"secret": "from-file"}))
    assert pt.load_secret(tmp_path / "s.json") == b"from-env"


def test_secret_file_is_read_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv(pt.SECRET_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"secret": "from-file"}))
    assert pt.load_secret(path) == b"from-file"


def test_secret_is_generated_and_persisted(monkeypatch, tmp_path):
    monkeypatch.delenv(pt.SECRET_ENV_VAR, raising=False)
    path = tmp_path / "nested" / "s.json"
    first = pt.load_secret(path)
    assert path.is_file()
    assert json.loads(path.read_text())["secret"] == first.decode()
    # A restart must reuse it: the install URL is replayed by `npx skills update`.
    assert pt.load_secret(path) == first


def test_generated_secret_file_is_not_world_readable(monkeypatch, tmp_path):
    monkeypatch.delenv(pt.SECRET_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    pt.load_secret(path)
    assert path.stat().st_mode & 0o077 == 0


def test_a_corrupt_secret_file_is_replaced_with_a_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv(pt.SECRET_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text("{not json")
    with caplog.at_level(
        logging.WARNING, logger="skillberry_store.tools.publish_tokens"
    ):
        secret = pt.load_secret(path)
    assert secret
    assert "Could not read npx publish secret" in caplog.text


def test_a_secret_file_without_a_secret_key_is_replaced(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv(pt.SECRET_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"other": "x"}))
    with caplog.at_level(
        logging.WARNING, logger="skillberry_store.tools.publish_tokens"
    ):
        assert pt.load_secret(path)
    assert "no 'secret' value" in caplog.text


def test_default_secret_path_honours_its_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(pt.SECRET_FILE_ENV_VAR, str(tmp_path / "custom.json"))
    assert pt._default_secret_path() == tmp_path / "custom.json"


def test_default_secret_path_falls_back_to_the_home_directory(monkeypatch):
    monkeypatch.delenv(pt.SECRET_FILE_ENV_VAR, raising=False)
    assert pt._default_secret_path().name == "wellknown_secret.json"
    assert pt._default_secret_path().parent.name == ".skillberry"
