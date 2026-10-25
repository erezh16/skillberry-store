# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Plugin contributions to exported skill packages, and claims on imported ones.

Two extension points, registered by plugins and consulted only while the
owning plugin is active (same registry pattern as ``plugins/events.py`` and
``modules/tool_providers.py``):

* an **export contributor** may add new files, frontmatter ``metadata``
  entries and a SKILL.md section to a skill package;
* an **import claimer** may claim specific files of a package being imported
  and return the tools to create for them; unclaimed files go through the
  default classification.

With no contributor active, export output is exactly what it was without this
module. A contributor or claimer that raises fails the whole export or import
(:class:`ContributionError`, naming the plugin); it never silently produces a
partial package.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExportContribution:
    files: Dict[str, str] = field(default_factory=dict)  # relative path -> text
    metadata: Dict[str, str] = field(default_factory=dict)  # frontmatter metadata
    skill_md_section: str = ""


class ExportContributor(Protocol):
    def contribute(
        self,
        skill: Dict[str, Any],
        tools: List[Dict[str, Any]],
        snippets: List[Dict[str, Any]],
    ) -> Optional[ExportContribution]: ...


@dataclass
class ImportClaim:
    claimed_paths: Set[str] = field(default_factory=set)
    # (tool data, module content, module filename), created via ToolsService.create
    tools: List[Tuple[Dict[str, Any], str, str]] = field(default_factory=list)


class ImportClaimer(Protocol):
    def claim(self, files: Dict[str, str]) -> Optional[ImportClaim]:
        """``files`` maps paths relative to the skill root to their text."""


class ContributionError(RuntimeError):
    """A plugin's contributor or claimer failed; the export/import is aborted."""

    def __init__(self, slug: str, detail: str):
        self.slug = slug
        super().__init__(f"Plugin '{slug}' failed: {detail}")


_contributors: List[Tuple[ExportContributor, str]] = []
_claimers: List[Tuple[ImportClaimer, str]] = []
_active_resolver: Optional[Callable[[str], bool]] = None


def register_export_contributor(contributor: ExportContributor, owner_slug: str) -> None:
    _contributors.append((contributor, owner_slug))


def register_import_claimer(claimer: ImportClaimer, owner_slug: str) -> None:
    _claimers.append((claimer, owner_slug))


def unregister(obj: Any) -> None:
    """Remove a contributor or claimer (used by tests)."""
    _contributors[:] = [(c, s) for c, s in _contributors if c is not obj]
    _claimers[:] = [(c, s) for c, s in _claimers if c is not obj]


def set_active_resolver(resolver: Optional[Callable[[str], bool]]) -> None:
    global _active_resolver
    _active_resolver = resolver


def _active(slug: str) -> bool:
    if _active_resolver is None:
        return True
    try:
        return bool(_active_resolver(slug))
    except Exception as e:
        logger.error(f"Active resolver failed for plugin '{slug}': {e}")
        return False


def active_contributors() -> List[Tuple[ExportContributor, str]]:
    return [(c, s) for c, s in _contributors if _active(s)]


def active_claimers() -> List[Tuple[ImportClaimer, str]]:
    return [(c, s) for c, s in _claimers if _active(s)]


def collect_export_contributions(
    skill: Dict[str, Any],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
) -> List[Tuple[ExportContribution, str]]:
    """Run every active contributor; returns the non-empty contributions."""
    out: List[Tuple[ExportContribution, str]] = []
    for contributor, slug in active_contributors():
        try:
            contribution = contributor.contribute(skill, tools, snippets)
        except Exception as e:
            logger.error(f"Export contributor of plugin '{slug}' raised: {e}", exc_info=True)
            raise ContributionError(slug, str(e)) from e
        if contribution is not None:
            out.append((contribution, slug))
    return out


def merge_contributed_metadata(
    base: Optional[Dict[str, Any]],
    contributions: List[Tuple[ExportContribution, str]],
) -> Optional[Dict[str, Any]]:
    """``base`` frontmatter metadata plus every contribution's; collisions fail."""
    merged: Dict[str, Any] = dict(base or {})
    for contribution, slug in contributions:
        for key, value in contribution.metadata.items():
            if key in merged:
                raise ContributionError(
                    slug, f"frontmatter metadata key '{key}' is already set"
                )
            merged[key] = value
    return merged or None


def add_contributed_files(
    files: Dict[str, str], contributions: List[Tuple[ExportContribution, str]]
) -> None:
    """Add every contributed file to ``files`` in place; collisions fail."""
    for contribution, slug in contributions:
        for path, content in contribution.files.items():
            if path in files or path.upper() == "SKILL.MD":
                raise ContributionError(slug, f"file '{path}' already exists in the package")
            files[path] = content


def claim_import_files(files: Dict[str, str]) -> Tuple[List[Tuple[ImportClaim, str]], Set[str]]:
    """Offer the package to every active claimer.

    Returns the claims and the union of claimed paths. A path claimed by an
    earlier claimer is not offered to later ones.
    """
    claims: List[Tuple[ImportClaim, str]] = []
    claimed: Set[str] = set()
    for claimer, slug in active_claimers():
        remaining = {p: c for p, c in files.items() if p not in claimed}
        try:
            claim = claimer.claim(remaining)
        except Exception as e:
            logger.error(f"Import claimer of plugin '{slug}' raised: {e}", exc_info=True)
            raise ContributionError(slug, str(e)) from e
        if claim is None:
            continue
        unknown = claim.claimed_paths - set(remaining)
        if unknown:
            raise ContributionError(slug, f"claimed unknown path(s) {sorted(unknown)}")
        claims.append((claim, slug))
        claimed |= claim.claimed_paths
    return claims, claimed
