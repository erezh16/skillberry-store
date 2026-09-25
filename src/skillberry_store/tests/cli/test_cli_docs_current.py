"""The CLI docs describe the native download, not a manual restish install.

docs/design/new_cli.md §8.3 #22, in the style of ``test_ui_docs_current.py``.

Docs rot silently, and these particular docs rot into a *dead end*: they used to
tell users to `go install github.com/rest-sh/restish@latest` before `sbs` would
work at all. That instruction now describes a prerequisite that does not exist,
and a user following it would install an unrelated binary and still not have
`sbs`. §9.1 makes updating them part of the change rather than follow-up
paperwork, and this is what enforces that.

The assertions are about *user-actionable content* — the install command, the
breaking change, the platform list — not prose, so ordinary editing does not
break them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_DOC = REPO_ROOT / "docs" / "cli.md"
CLI_PAGE = REPO_ROOT / "site" / "cli.html"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# The instructions that told users to install restish by hand. Each is a shape
# that only appears when someone is being asked to obtain restish themselves.
STALE_INSTALL_MARKERS = (
    "go install github.com/rest-sh/restish",
    "github.com/rest-sh/restish/releases",
    "restish to be installed",
    "requires `restish`",
    "requires <code>restish</code>",
)


@pytest.fixture(scope="module")
def cli_doc() -> str:
    assert CLI_DOC.is_file(), f"{CLI_DOC} is missing"
    return CLI_DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cli_page() -> str:
    assert CLI_PAGE.is_file(), f"{CLI_PAGE} is missing"
    return CLI_PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def changelog() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


@pytest.mark.parametrize("doc_name", ["docs/cli.md", "site/cli.html"])
def test_no_manual_restish_install_instructions(doc_name, cli_doc, cli_page):
    text = cli_doc if doc_name.endswith(".md") else cli_page
    found = [m for m in STALE_INSTALL_MARKERS if m in text]
    assert not found, (
        f"{doc_name} still tells users to install restish by hand: {found}. "
        f"That prerequisite no longer exists — `sbs` is a single static binary "
        f"that embeds restish as a library (docs/design/new_cli.md §4.6)."
    )


@pytest.mark.parametrize("doc_name", ["docs/cli.md", "site/cli.html"])
def test_documents_the_store_install_script(doc_name, cli_doc, cli_page):
    text = cli_doc if doc_name.endswith(".md") else cli_page
    assert "/cli/install.sh" in text, (
        f"{doc_name} does not document the store's install script, which is the "
        f"primary way a user gets the CLI."
    )


@pytest.mark.parametrize("doc_name", ["docs/cli.md", "site/cli.html"])
def test_documents_the_pip_replacement_package(doc_name, cli_doc, cli_page):
    """The breaking change is only actionable if the new package is named."""
    text = cli_doc if doc_name.endswith(".md") else cli_page
    assert "skillberry-store-cli" in text, (
        f"{doc_name} does not name skillberry-store-cli. `pip install "
        f"skillberry-store-sdk` no longer provides `sbs`, so a reader needs the "
        f"replacement spelled out."
    )


def test_docs_mention_the_self_download_verbs(cli_doc):
    for verb in ("download-cli", "self-update"):
        assert verb in cli_doc, f"docs/cli.md does not document `sbs {verb}`"


def test_docs_list_the_supported_platforms(cli_doc):
    """A user has to be able to tell whether their machine is covered."""
    for platform in (
        "linux-amd64",
        "linux-arm64",
        "darwin-amd64",
        "darwin-arm64",
        "windows-amd64",
    ):
        assert platform in cli_doc, f"docs/cli.md does not list {platform}"


def test_docs_cover_the_unsigned_binary_friction(cli_doc):
    """B17: macOS quarantine and SmartScreen are documented, not worked around."""
    assert "quarantine" in cli_doc, (
        "docs/cli.md does not mention macOS quarantine. The binaries are "
        "unsigned, so a browser download is quarantined and the user needs the "
        "remedy."
    )
    assert "com.apple.quarantine" in cli_doc, (
        "give the actual xattr command; 'macOS may complain' is not actionable"
    )


def test_docs_describe_the_branded_config_paths(cli_doc):
    """§4.4: users need to know where config and cache now live."""
    assert "~/.config/sbs" in cli_doc, "docs/cli.md does not give the config directory"
    assert "sbs cli doctor" in cli_doc, (
        "docs/cli.md should point at `sbs cli doctor`, which prints every "
        "resolved path — the answer to most 'where did it put X' questions"
    )


def test_docs_explain_the_config_migration(cli_doc):
    """A user with an existing restish config should learn what happened to it."""
    assert "restish.json" in cli_doc, (
        "docs/cli.md does not mention the one-time migration out of "
        "~/.config/restish/restish.json, so an upgrading user cannot tell whether "
        "their existing registration still applies"
    )


def test_changelog_names_the_breaking_identifier(changelog):
    """§9.1: a deployer must be able to search for the thing that broke.

    ``test_changelog.py`` guards the file's structure; this guards *this*
    migration's searchable identifier, the way that file does for `:latest-full`
    and `ENABLE_UI`.
    """
    assert "sbs console script" in changelog, (
        "the CHANGELOG does not contain the identifier 'sbs console script'. "
        "That is the phrase a deployer whose CI just lost `sbs` would search for."
    )
    assert "skillberry-store-cli" in changelog, (
        "the CHANGELOG must name the replacement package, or the note is not "
        "actionable"
    )
    assert "SDK_PY_CLI" in changelog, (
        "name the flag, so a sibling asset in the shared subtree can tell whether "
        "this affects them"
    )
