# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Where the build system under test lives, from inside its own test folder.

Two roots matter here, because skillberry-common is a git subtree rather than a
standalone checkout:

``COMMON_ROOT``
    This repository — the scripts and ``.mk`` files being tested. Always real.

``PROJECT_ROOT``
    The project that embeds the subtree at ``<project>/skillberry-common`` and
    whose own ``Makefile`` includes ours. Only there can make resolve a full rule
    database, and only there do project-owned makefiles such as ``.mk/dev.mk``
    exist — so tests that need either must carry ``requires_project``. Cloned on
    its own, this repository has no such parent and those tests skip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

COMMON_ROOT = Path(__file__).resolve().parents[1]

# Where an embedding project would be. Kept a Path either way so module-level
# constants can be spelled out plainly; `requires_project` is what guards use.
PROJECT_ROOT = COMMON_ROOT.parent


def _embeds_this_subtree(project: Path) -> bool:
    """Does `project` drive its build through the makefiles in COMMON_ROOT?

    The root Makefile of an embedding project sets SB_COMMON_PATH to the subtree
    path and includes `$(SB_COMMON_PATH)/Makefile`; nothing else does.
    """
    makefile = project / "Makefile"
    return makefile.is_file() and "SB_COMMON_PATH" in makefile.read_text()


requires_project = pytest.mark.skipif(
    not _embeds_this_subtree(PROJECT_ROOT),
    reason="no embedding project: skillberry-common is checked out on its own",
)
