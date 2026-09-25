"""The reserved-name guard for the native CLI's promoted command surface.

docs/design/new_cli.md §8.1 #5. The Go CLI embeds restish with
``SetCommandSurface{PromotedAPI: "store", SupportCommandNamespace: "cli"}``, so
every generated operation lands at the *root* of the command tree — ``sbs
list-skills``, not ``sbs store list-skills`` (§4.2).

That is the whole ergonomic point, and it creates one failure mode worth a test:
if an endpoint's ``x-cli-name`` ever equals the support namespace, restish
refuses to start with

    command surface: promoted operation "cli" collides with support command
    namespace "cli"; choose another SupportCommandNamespace or hide support
    commands

which is a *runtime* failure of the shipped binary, triggered by an endpoint
added on the Python side. This test moves that discovery to PR time, where the
person who added the endpoint can see it, rather than to release time — or to a
user's terminal.

The same applies to the verbs the CLI handles itself before restish parses argv
(``connect``, ``download-cli``, ``self-update``): an operation with one of those
names would be silently shadowed, which is worse than a collision error because
nothing would report it at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_GO_DIR = REPO_ROOT / "cli" / "go"


def _cli_names_from_source() -> set[str]:
    """Collect every ``x-cli-name`` declared under fast_api/.

    Read from the source rather than from a constructed ``SBS`` app on purpose:
    building the app pulls in the vector DB, the plugin loader and the object
    handlers, which makes a guard against a one-line typo cost seconds and
    depend on unrelated subsystems being importable. The marker is a literal in
    an ``openapi_extra`` dict, so grepping for it is both sufficient and exact.
    """
    names: set[str] = set()
    pattern = re.compile(r'"x-cli-name":\s*"([^"]+)"')
    for path in (REPO_ROOT / "src" / "skillberry_store" / "fast_api").rglob("*.py"):
        names.update(pattern.findall(path.read_text(encoding="utf-8")))
    return names


def _go_const(name: str) -> str:
    """Read a single-quoted Go string constant out of the CLI sources.

    Keeps this test honest about what the binary actually uses: hardcoding
    ``"cli"`` here would keep passing if someone changed the namespace in Go,
    which is precisely the change that could introduce a collision.
    """
    pattern = re.compile(rf'^const {re.escape(name)}\s*=\s*"([^"]*)"', re.MULTILINE)
    for path in CLI_GO_DIR.glob("*.go"):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    raise AssertionError(f"could not find `const {name}` in {CLI_GO_DIR}")


def _local_verbs() -> set[str]:
    """The verbs localVerb() claims before restish sees argv (cli/go/verbs.go)."""
    source = (CLI_GO_DIR / "verbs.go").read_text(encoding="utf-8")
    body = re.search(r"switch argv\[1\] \{(.*?)\n\t\}", source, re.DOTALL)
    assert body, "could not locate the localVerb switch in cli/go/verbs.go"
    return set(re.findall(r'case "([^"]+)":', body.group(1)))


# restish's own support commands, which a promoted surface keeps reachable. With
# a SupportCommandNamespace set they live under it, so only the namespace itself
# can collide — but if that namespace is ever removed, these become root
# commands and every one of them becomes a collision risk. Listed so the test
# stays correct under that change.
RESTISH_SUPPORT_COMMANDS = frozenset(
    {"auth", "cache", "completion", "config", "doctor", "version"}
)


def test_no_cli_name_collides_with_support_namespace():
    """An operation named like the support namespace makes the binary refuse to start."""
    namespace = _go_const("supportNamespace")
    collisions = sorted(n for n in _cli_names_from_source() if n == namespace)
    assert not collisions, (
        f"x-cli-name {collisions} collides with the CLI's support command "
        f"namespace {namespace!r}. The shipped binary would refuse to start. "
        f"Rename the endpoint's x-cli-name, or change supportNamespace in "
        f"cli/go/main.go."
    )


def test_no_cli_name_is_silently_shadowed_by_a_local_verb():
    """A local verb shadows a generated operation with no error at all."""
    verbs = _local_verbs()
    assert verbs, "expected localVerb to claim at least one verb"

    cli_names = _cli_names_from_source()
    # `login` and `logout` are deliberately shadowed: they exist as generated
    # operations (POST /auth/login, POST /auth/logout) but are dispatched as the
    # thin verbs of §4.3, because typing a JSON body to log in — with the
    # password on argv — is exactly what the shim intercepted them to avoid.
    intentional = {"login", "logout"}
    shadowed = sorted((cli_names & verbs) - intentional)
    assert not shadowed, (
        f"x-cli-name {shadowed} is shadowed by a local verb in cli/go/verbs.go, "
        f"so typing it would never reach the store and nothing would report it. "
        f"Rename the endpoint, or remove the verb."
    )


def test_login_and_logout_are_still_the_intentionally_shadowed_pair():
    """Pin the exception above, so it cannot quietly grow.

    If a future endpoint is added whose ``x-cli-name`` happens to match a verb,
    the previous test must fail rather than be waived by an exception list that
    someone extended without thinking about it.
    """
    cli_names = _cli_names_from_source()
    for name in ("login", "logout"):
        assert name in cli_names, (
            f"{name!r} is no longer a generated operation, so the CLI no longer "
            f"needs to shadow it; drop it from the `intentional` set and from "
            f"cli/go/login.go."
        )


def test_support_commands_do_not_collide_when_namespaced():
    """With a namespace configured, restish's support commands cannot collide.

    This documents *why* the collision surface is only one name: a namespace
    moves `auth`/`cache`/`config`/`doctor`/`completion`/`version` under it. Drop
    the namespace and all six return to the root, so this assertion is what
    tells you the guard above would need widening.
    """
    namespace = _go_const("supportNamespace")
    assert namespace, (
        "supportNamespace is empty, so restish's support commands sit at the "
        "root and any of "
        f"{sorted(RESTISH_SUPPORT_COMMANDS)} could collide with an operation. "
        "Widen test_no_cli_name_collides_with_support_namespace to check all of "
        "them."
    )
    overlap = sorted(_cli_names_from_source() & RESTISH_SUPPORT_COMMANDS)
    if overlap:
        pytest.fail(
            f"x-cli-name {overlap} shares a name with a restish support command. "
            f"It is safe today only because supportNamespace={namespace!r} moves "
            f"them off the root; it would break if that were removed."
        )


def test_promoted_api_name_is_not_itself_an_operation():
    """``store`` is the configured API key, not a command, but keep them distinct.

    A generated operation named ``store`` would read as if it addressed the API
    itself, which is the two-level shape (``sbs store list-skills``) this design
    removed.
    """
    api_name = _go_const("apiName")
    assert api_name not in _cli_names_from_source(), (
        f"an operation is named {api_name!r}, the promoted API's own key; "
        f"rename it to keep the root surface unambiguous."
    )
