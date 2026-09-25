# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Scoped publish tokens — docs/design/npx.md §4.3.3 / §4.3.7."""

from __future__ import annotations

import json
import logging

import pytest

from skillberry_store.tools import publish_refs as pr

SEED = b"a-test-seed"


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #
def test_ref_is_43_urlsafe_base64_chars():
    """§5.10 #5: base64url of the full digest, ``=`` stripped."""
    token = pr.derive_ref(SEED, "alice", pr.skill_scope("pdf-forms"))
    assert len(token) == pr.REF_LENGTH == 43
    assert "=" not in token
    assert all(c.isalnum() or c in "-_" for c in token)


def test_ref_is_stable_for_the_same_inputs():
    """The URL lives in a lockfile and is replayed by every `npx skills update`."""
    first = pr.derive_ref(SEED, "alice", "skill:a")
    second = pr.derive_ref(SEED, "alice", "skill:a")
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
def test_ref_differs_per_tenant_and_per_scope(tenant, scope):
    base = pr.derive_ref(SEED, "alice", "skill:a")
    assert pr.derive_ref(SEED, tenant, scope) != base


def test_rotating_the_seed_invalidates_every_ref():
    """The global revoke the derived scheme has — a control, not a hazard."""
    before = pr.derive_ref(SEED, "alice", "skill:a")
    assert pr.derive_ref(b"rotated", "alice", "skill:a") != before


def test_ref_does_not_contain_the_tenant_id():
    """Otherwise the username reaches a shell history and Vercel's telemetry."""
    token = pr.derive_ref(SEED, "alice", "skill:pdf-forms")
    assert "alice" not in token
    assert "pdf-forms" not in token


def test_scope_helpers():
    assert pr.skill_scope("pdf-forms") == "skill:pdf-forms"
    assert pr.namespace_scope("data-eng") == "ns:data-eng"
    assert pr.SCOPE_ALL == "*"


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def test_match_ref_recovers_the_tenant_and_scope():
    token = pr.derive_ref(SEED, "bob", "skill:pdf-forms")
    got = pr.match_ref(
        token, SEED, ["alice", "bob"], ["skill:pdf-forms", "skill:other"]
    )
    assert got == ("bob", "skill:pdf-forms")


def test_match_ref_rejects_a_scope_outside_the_candidate_set():
    """Skill A's token must not open skill B's URL (§4.3.7, test #21)."""
    token = pr.derive_ref(SEED, "bob", "skill:a")
    assert pr.match_ref(token, SEED, ["bob"], ["skill:b"]) is None


def test_match_ref_rejects_a_tenant_outside_the_candidate_set():
    token = pr.derive_ref(SEED, "carol", "skill:a")
    assert pr.match_ref(token, SEED, ["alice", "bob"], ["skill:a"]) is None


def test_match_ref_rejects_a_rotated_seed():
    token = pr.derive_ref(SEED, "bob", "skill:a")
    assert pr.match_ref(token, b"rotated", ["bob"], ["skill:a"]) is None


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 43])
def test_match_ref_rejects_junk(token):
    assert pr.match_ref(token, SEED, ["bob"], ["skill:a"]) is None


def test_match_ref_with_no_candidates_is_none():
    token = pr.derive_ref(SEED, "bob", "skill:a")
    assert pr.match_ref(token, SEED, [], ["skill:a"]) is None
    assert pr.match_ref(token, SEED, ["bob"], []) is None


def test_match_ref_handles_multiple_scopes_per_tenant():
    tenants = ["alice", "bob"]
    scopes = ["skill:a", "ns:data", "*"]
    for tenant in tenants:
        for scope in scopes:
            token = pr.derive_ref(SEED, tenant, scope)
            assert pr.match_ref(token, SEED, tenants, scopes) == (tenant, scope)


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
    assert pr.candidate_tenants(cfg) == ["skillberry", "skillberry-admin"]


def test_candidate_tenants_deduplicates():
    from skillberry_store.access_control.config import AccessControlConfig, User

    cfg = AccessControlConfig(
        users=[
            User(username="a", tenant_id="shared", password_hash="x"),
            User(username="b", tenant_id="shared", password_hash="x"),
        ]
    )
    assert pr.candidate_tenants(cfg) == ["shared"]


def test_candidate_tenants_of_none_is_empty():
    assert pr.candidate_tenants(None) == []


# --------------------------------------------------------------------------- #
# Seed loading and persistence
# --------------------------------------------------------------------------- #
def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(pr.SEED_ENV_VAR, "from-env")
    (tmp_path / "s.json").write_text(json.dumps({"seed": "from-file"}))
    assert pr.load_seed(tmp_path / "s.json") == b"from-env"


def test_seed_file_is_read_when_present(monkeypatch, tmp_path):
    monkeypatch.delenv(pr.SEED_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"seed": "from-file"}))
    assert pr.load_seed(path) == b"from-file"


def test_seed_is_generated_and_persisted(monkeypatch, tmp_path):
    monkeypatch.delenv(pr.SEED_ENV_VAR, raising=False)
    path = tmp_path / "nested" / "s.json"
    first = pr.load_seed(path)
    assert path.is_file()
    assert json.loads(path.read_text())["seed"] == first.decode()
    # A restart must reuse it: the install URL is replayed by `npx skills update`.
    assert pr.load_seed(path) == first


def test_generated_seed_file_is_not_world_readable(monkeypatch, tmp_path):
    monkeypatch.delenv(pr.SEED_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    pr.load_seed(path)
    assert path.stat().st_mode & 0o077 == 0


def test_a_corrupt_seed_file_is_replaced_with_a_warning(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv(pr.SEED_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text("{not json")
    with caplog.at_level(
        logging.WARNING, logger="skillberry_store.tools.publish_refs"
    ):
        seed = pr.load_seed(path)
    assert seed
    assert "Could not read npx publish seed" in caplog.text


def test_a_seed_file_without_a_seed_value_is_replaced(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv(pr.SEED_ENV_VAR, raising=False)
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"other": "x"}))
    with caplog.at_level(
        logging.WARNING, logger="skillberry_store.tools.publish_refs"
    ):
        assert pr.load_seed(path)
    assert "no 'seed' value" in caplog.text


def test_default_seed_path_honours_its_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv(pr.SEED_FILE_ENV_VAR, str(tmp_path / "custom.json"))
    assert pr._default_seed_path() == tmp_path / "custom.json"


def test_default_seed_path_falls_back_to_the_home_directory(monkeypatch):
    monkeypatch.delenv(pr.SEED_FILE_ENV_VAR, raising=False)
    assert pr._default_seed_path().name == "publish_seed.json"
    assert pr._default_seed_path().parent.name == ".skillberry"
