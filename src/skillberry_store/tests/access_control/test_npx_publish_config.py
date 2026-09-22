"""The ``npx_publish`` switch and the ``/pub/*`` allow-list entry.

docs/design/npx.md §5.12 and §6.4. The switch lives in the access-control config
rather than an env var because it *is* an access-control decision: it is the one
setting that makes skill content reachable without a session, so it belongs
beside ``unauthenticated_paths`` where the rest of them are reviewed.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from skillberry_store.access_control.config import (
    _DEFAULT_UNAUTH_PATHS,
    load_config,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _write(tmp_path, contents: str) -> str:
    path = tmp_path / "acl.yaml"
    path.write_text(textwrap.dedent(contents))
    return str(path)


def test_default_in_code_is_off():
    """Fail-closed, and deliberately not mode-dependent."""
    assert load_config("/nonexistent/acl.yaml").npx_publish is False


def test_an_older_config_file_keeps_loading_with_the_feature_off(tmp_path):
    """Purely additive: the loader reads known keys and ignores unknown ones."""
    cfg = load_config(_write(tmp_path, "mode: disabled\n"))
    assert cfg.npx_publish is False


@pytest.mark.parametrize("value", ["true", "yes", "on", '"1"'])
def test_truthy_spellings_enable_it(tmp_path, value):
    cfg = load_config(_write(tmp_path, f"mode: disabled\nnpx_publish: {value}\n"))
    assert cfg.npx_publish is True


@pytest.mark.parametrize("value", ["false", "no", "off", "null"])
def test_falsy_spellings_leave_it_off(tmp_path, value):
    cfg = load_config(_write(tmp_path, f"mode: disabled\nnpx_publish: {value}\n"))
    assert cfg.npx_publish is False


def test_a_junk_value_fails_closed_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="skillberry_store.access_control.config"):
        cfg = load_config(_write(tmp_path, "mode: disabled\nnpx_publish: maybe\n"))
    assert cfg.npx_publish is False
    assert "npx_publish" in caplog.text


def test_pub_prefix_is_in_the_built_in_allowlist():
    assert "GET /pub/*" in _DEFAULT_UNAUTH_PATHS


def test_the_allowlist_entry_matches_index_alias_and_artifact():
    """One glob covers all three, because all three live under /pub/ (§6.4)."""
    cfg = load_config("/nonexistent/acl.yaml")
    for path in (
        "/pub/TOKEN/.well-known/agent-skills/index.json",
        "/pub/TOKEN/.well-known/skills/index.json",
        "/pub/TOKEN/.well-known/agent-skills/pdf-forms.zip",
        "/pub/pdf-forms/.well-known/agent-skills/index.json",
    ):
        assert cfg.is_unauthenticated("GET", path), path


def test_the_allowlist_entry_does_not_widen_anything_else():
    cfg = load_config("/nonexistent/acl.yaml")
    assert not cfg.is_unauthenticated("GET", "/skills/")
    assert not cfg.is_unauthenticated("GET", "/publish/x")
    # Only GET: a write method under the same prefix is not allow-listed.
    assert not cfg.is_unauthenticated("POST", "/pub/TOKEN/anything")
    assert not cfg.is_unauthenticated("DELETE", "/pub/TOKEN/anything")


# --------------------------------------------------------------------------- #
# The shipped files (the §6.4 "three files, same commit" requirement)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "filename,expected",
    [
        # mode: disabled — nothing is protected there anyway, so a bare dev
        # checkout is usable out of the box.
        ("access_control_config.yaml", True),
        # The demo operator opts in deliberately.
        ("access_control_config.yaml.standalone", False),
    ],
)
def test_shipped_configs_declare_the_switch(filename, expected):
    cfg = load_config(str(REPO_ROOT / filename))
    assert cfg.npx_publish is expected


@pytest.mark.parametrize(
    "filename",
    ["access_control_config.yaml", "access_control_config.yaml.standalone"],
)
def test_shipped_configs_allowlist_the_pub_prefix(filename):
    """Present unconditionally: harmless when the routes are absent, and it
    makes enabling the feature a one-line edit rather than two (§5.12)."""
    cfg = load_config(str(REPO_ROOT / filename))
    assert "GET /pub/*" in cfg.unauthenticated_paths
    assert cfg.is_unauthenticated(
        "GET", "/pub/TOKEN/.well-known/agent-skills/index.json"
    )
