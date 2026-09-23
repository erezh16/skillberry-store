# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Well-known skill discovery for ``npx skills add`` (docs/design/npx.md).

Pure functions plus a process-local artifact cache — no FastAPI import, so this
module is unit-testable without an app. The HTTP surface lives in
:mod:`skillberry_store.fast_api.wellknown_api`.

Three contracts from the consuming CLI drive everything here (§1.3, §1.4):

* an index entry's ``name`` must match ``^[a-z0-9-]{1,64}$`` with no ``--`` and
  no edge hyphen, so a free-form SBS name is projected to a **slug**;
* an entry's ``digest`` must be the sha256 of the exact bytes the artifact
  endpoint will return, or the skill is silently dropped from the install;
* the archive must carry a root-level ``SKILL.md`` and no path the CLI's
  ``normalizeArchivePath`` rejects — one bad entry aborts the whole archive.

The slug is a **projection, not a second identity** (§4.0.2): derived from
``name`` at read time, never persisted, and not resolvable through any other SBS
API. Its only jobs are to be the index entry name, the artifact URL segment and
the directory name on the client's disk.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from skillberry_store.tools.anthropic.exporter import (
    _build_file_structure,
    build_deterministic_zip,
    extract_file_path_from_tags,
    normalize_file_path,
    strip_skill_prefix,
    unsafe_archive_paths,
)

logger = logging.getLogger(__name__)

# Compared by the CLI as a literal string constant — it is never fetched — so
# the value must be reproduced byte-exactly (§1.3).
DISCOVERY_SCHEMA_V2 = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"

# A v0.2.0 entry with a description longer than this is invalid, so truncate
# rather than reject (§5.3 #2).
MAX_DESCRIPTION_CHARS = 1024

# The CLI's own archive guards (§1.4). Serving an artifact that breaches either
# one means the skill is rejected on the client with no useful diagnostic, so we
# decline to publish it instead.
MAX_ARCHIVE_FILES = 1000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024

# Default bound on the artifact cache (§5.8 #2). A miss is cheap; an unbounded
# cache pinning every archive for the process lifetime is not.
DEFAULT_CACHE_MAX_BYTES = 64 * 1024 * 1024

# The agent an install command targets unless the caller picks another. `-a`
# must always be explicit: `-y` with no detected agent installs into ~75 agent
# directories (§4.3.1).
DEFAULT_AGENT = "claude-code"

# Namespaces are ordinary tags under this prefix (services/facets.py).
NAMESPACE_TAG_PREFIX = "namespace:"

#: Per-skill opt-out. A skill carrying this ordinary tag is still *published* —
#: it has to be, or its own install URL would stop working — but its emitted
#: frontmatter gets ``metadata.internal: true``, which the CLI honours by hiding
#: the skill from a multi-entry install list unless ``INSTALL_INTERNAL_SKILLS=1``
#: (§1.4, §4.3). A tag rather than a new manifest field so it is visible and
#: filterable in the UI, and needs no migration.
INTERNAL_TAG = "npx-internal"

#: Restricts which namespaces a ``ns:`` scope may name. Unset means "any
#: namespace that exists" (§4.3).
NAMESPACES_ENV_VAR = "SBS_WELLKNOWN_NAMESPACES"


def allowed_namespaces() -> Optional[List[str]]:
    """The operator's namespace allowlist, or ``None`` when unrestricted.

    An empty or whitespace-only value is treated as unset rather than as "no
    namespaces at all": the latter is a configuration mistake that would look
    like the feature being broken, and an operator who wants no namespace
    surface simply does not hand out ``ns:`` URLs.
    """
    raw = os.environ.get(NAMESPACES_ENV_VAR, "")
    names = [part.strip() for part in raw.split(",") if part.strip()]
    return names or None


def is_internal(skill: Dict[str, Any]) -> bool:
    """Whether ``skill`` opted out of being offered in a multi-entry install."""
    return INTERNAL_TAG in (skill.get("tags") or [])


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class WellKnownError(Exception):
    """Base class for "this skill cannot be published" conditions."""


class UnsafeArchivePathError(WellKnownError):
    """An archive path the consuming CLI would reject (§5.7)."""

    def __init__(self, paths: List[str]):
        super().__init__(f"unsafe archive path(s): {', '.join(paths)}")
        self.paths = paths


class ArchiveTooLargeError(WellKnownError):
    """An archive beyond the CLI's file-count or unpacked-size cap (§5.3 #5)."""


@dataclass(frozen=True)
class PublishedSkill:
    """One index entry plus the exact bytes its digest covers."""

    slug: str  # CLI-legal install name
    uuid: str  # SBS identity
    name: str  # original SBS name
    description: str  # truncated to MAX_DESCRIPTION_CHARS
    digest: str  # "sha256:<64 hex>"
    payload: bytes  # the exact archive bytes the digest covers

    def index_entry(self) -> Dict[str, Any]:
        """This skill as a v0.2.0 discovery-index entry.

        ``url`` is deliberately **relative**: the CLI resolves it against the
        URL the index was fetched from, so the ``/pub/{ref}/`` prefix carries
        forward on its own. The index body therefore contains no token, the
        artifact inherits the same gate, and nothing hard-codes a hostname the
        store may not know (§5.14).
        """
        return {
            "name": self.slug,
            "description": self.description,
            "type": "archive",
            "url": f"{self.slug}.zip",
            "digest": self.digest,
        }


# --------------------------------------------------------------------------- #
# Slugs
# --------------------------------------------------------------------------- #
def to_slug(name: str) -> str:
    """Project a free-form SBS name onto the CLI's ``^[a-z0-9-]+$`` grammar.

    Mirrors the CLI's own normalisation (§4.2). Returns ``""`` when nothing
    survives — the caller skips such a skill and logs at WARNING, because there
    is no name it could publish it under.
    """
    s = re.sub(r"[\s_]+", "-", (name or "").lower())
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64].strip("-")


def is_valid_slug(slug: str) -> bool:
    """Whether ``slug`` passes the CLI's entry-name validation (§1.3)."""
    return bool(slug) and len(slug) <= 64 and bool(_SLUG_RE.match(slug))


def _suffixed(base: str, suffix: str) -> str:
    """``base-suffix``, trimmed so the result stays a valid 64-char slug."""
    room = 64 - len(suffix) - 1
    if room <= 0:
        return ""
    return f"{base[:room].strip('-')}-{suffix}"


def _slug_sort_key(skill: Dict[str, Any]) -> Tuple[str, str]:
    """Oldest ``created_at`` first; uuid breaks a tie (§5.6).

    Sorting by ``(name, uuid)`` instead would be deterministic for a *fixed*
    store but not **stable** across mutations: adding a skill whose uuid sorts
    lower would take the bare slug and push an existing skill onto a suffixed
    one, silently orphaning an installed copy in the user's
    ``skills-lock.json``.
    """
    return (str(skill.get("created_at") or ""), str(skill.get("uuid") or ""))


def assign_slugs(skills: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map each publishable skill to a stable, unique, CLI-legal slug.

    Collisions are real: ``"PDF Forms"`` and ``"pdf-forms"`` both slug to
    ``pdf-forms``. The oldest skill keeps the bare slug; later ones take
    ``-{uuid[:4]}``, then ``-{uuid[:8]}``, then the full uuid. Bare slugs are
    all assigned before any suffixed one, so a suffixed slug can never displace
    a bare one (§5.6).

    Returns:
        ``{slug: skill_dict}``. A skill whose name slugs to ``""`` is omitted
        and logged at WARNING.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for skill in skills:
        base = to_slug(str(skill.get("name") or ""))
        if not base:
            logger.warning(
                "Not publishing skill %s for npx: name %r has no valid slug",
                skill.get("uuid"),
                skill.get("name"),
            )
            continue
        groups.setdefault(base, []).append(skill)

    assigned: Dict[str, Dict[str, Any]] = {}
    leftovers: List[Tuple[str, Dict[str, Any]]] = []

    # Pass 1 — every bare slug, so pass 2 cannot take one that is owed.
    for base in sorted(groups):
        members = sorted(groups[base], key=_slug_sort_key)
        assigned[base] = members[0]
        leftovers.extend((base, m) for m in members[1:])

    # Pass 2 — the runners-up, in the same stable order.
    for base, skill in sorted(leftovers, key=lambda pair: _slug_sort_key(pair[1])):
        uuid = str(skill.get("uuid") or "")
        for candidate in (
            _suffixed(base, uuid[:4]),
            _suffixed(base, uuid[:8]),
            _suffixed(base, uuid),
        ):
            if candidate and is_valid_slug(candidate) and candidate not in assigned:
                assigned[candidate] = skill
                break
        else:
            logger.warning(
                "Not publishing skill %s (%r) for npx: no free slug near %r",
                uuid,
                skill.get("name"),
                base,
            )
    return assigned


# --------------------------------------------------------------------------- #
# Which skills exist to publish
# --------------------------------------------------------------------------- #
def head_skills(service: Any) -> List[Dict[str, Any]]:
    """Every skill that is the HEAD of its version chain.

    Never ``list_all()``: that applies no HEAD filter and returns *every*
    object on every ``parent`` chain, so an index built off it would publish one
    entry per revision — all sharing a name, hence all slugging identically
    (§5.5). ``name_cache`` holds exactly one uuid per name, and it is the same
    resolution ``GET /skills/{name}`` performs, so "the skill the index
    publishes" and "the skill the API returns" stay the same object by
    construction.
    """
    handler = service.handler
    heads: List[Dict[str, Any]] = []
    for name in sorted(handler.name_cache.get_all_names()):
        uuid = handler.name_cache.get_head(name)
        if not uuid:
            continue
        try:
            heads.append(service.get(uuid, fields="wide"))
        except Exception as e:  # noqa: BLE001 — one unreadable skill must not
            logger.warning("Skipping skill %s (%s) for npx: %s", name, uuid, e)
    return heads


def is_head(service: Any, skill: Dict[str, Any]) -> bool:
    """Whether ``skill`` is the HEAD its name currently resolves to (§5.10 #7)."""
    name = skill.get("name")
    if not name:
        return False
    return service.handler.name_cache.get_head(str(name)) == skill.get("uuid")


def namespaces_of(skill: Dict[str, Any]) -> List[str]:
    """The namespaces ``skill`` is tagged with (``namespace:<x>`` tags)."""
    return [
        tag[len(NAMESPACE_TAG_PREFIX) :]
        for tag in (skill.get("tags") or [])
        if isinstance(tag, str) and tag.startswith(NAMESPACE_TAG_PREFIX)
    ]


def authorized_skills(
    service: Any,
    subject: Any,
    cfg: Any,
    scope: str = "*",
) -> List[Dict[str, Any]]:
    """The HEAD skills ``subject`` is authorized to see within ``scope``.

    The single place a multi-entry index's contents are decided (§4.3.9). It is
    written as "filter through the authorization model" rather than "return
    everything, because visibility is binary today": ``Rule`` carries no
    instance/tag/namespace field, so today the filter is a no-op once
    ``skills:list`` is granted — but a future per-namespace role binding then
    narrows the published set automatically instead of silently
    over-publishing.

    Args:
        service: The ``SkillsService``.
        subject: The runtime ``Subject`` the token resolved to, or ``None``
            when no auth layer exists (``mode: disabled``).
        cfg: The access-control config, or ``None`` in ``mode: disabled``.
        scope: ``"*"`` for everything visible, or ``"ns:<name>"`` for one
            namespace.

    Returns:
        The authorized skills, or ``[]`` when the subject may not list skills.
    """
    if cfg is not None and getattr(cfg, "mode", "disabled") != "disabled":
        from skillberry_store.access_control import pdp

        if subject is None or not pdp.authorize(subject, "skills", "list", cfg).allowed:
            return []
    skills = head_skills(service)
    if scope.startswith("ns:"):
        namespace = scope[3:]
        allowed = allowed_namespaces()
        if allowed is not None and namespace not in allowed:
            logger.info(
                "npx: namespace %r is not in %s; publishing nothing for it",
                namespace,
                NAMESPACES_ENV_VAR,
            )
            return []
        skills = [s for s in skills if namespace in namespaces_of(s)]
    return skills


# --------------------------------------------------------------------------- #
# Descriptions and archive safety
# --------------------------------------------------------------------------- #
def normalise_description(text: Any, name: str) -> str:
    """A non-empty description of at most :data:`MAX_DESCRIPTION_CHARS` chars.

    Both bounds are v0.2.0 validity rules: an empty description invalidates the
    entry, and so does one over 1024 characters (§1.3). Truncation prefers a
    word boundary so the text stays readable.
    """
    value = text.strip() if isinstance(text, str) else ""
    if not value:
        value = f"Skill: {name}"
    if len(value) <= MAX_DESCRIPTION_CHARS:
        return value
    cut = value[:MAX_DESCRIPTION_CHARS]
    boundary = cut.rfind(" ")
    if boundary > MAX_DESCRIPTION_CHARS // 2:
        cut = cut[:boundary]
    return cut.rstrip()


def safe_archive_paths(files: Dict[str, bytes]) -> Dict[str, bytes]:
    """Return ``files`` unchanged, or raise if any path the CLI rejects is in it.

    Rejecting here rather than filtering is deliberate: a skill that would
    silently lose a file is worse than one the operator is told is
    unpublishable, and the CLI aborts extraction of the *whole* archive on one
    bad entry anyway (§5.7).

    Raises:
        UnsafeArchivePathError: naming every offending path.
    """
    offenders = unsafe_archive_paths(files)
    if offenders:
        raise UnsafeArchivePathError(offenders)
    return files


def check_archive_limits(files: Dict[str, bytes]) -> Dict[str, bytes]:
    """Return ``files`` unchanged, or raise if it breaches a CLI cap (§1.4).

    Raises:
        ArchiveTooLargeError: on more than :data:`MAX_ARCHIVE_FILES` entries or
            more than :data:`MAX_ARCHIVE_BYTES` unpacked.
    """
    if len(files) > MAX_ARCHIVE_FILES:
        raise ArchiveTooLargeError(
            f"{len(files)} files exceeds the CLI's {MAX_ARCHIVE_FILES}-file cap"
        )
    total = sum(len(v) for v in files.values())
    if total > MAX_ARCHIVE_BYTES:
        raise ArchiveTooLargeError(
            f"{total} unpacked bytes exceeds the CLI's {MAX_ARCHIVE_BYTES}-byte cap"
        )
    return files


def offending_file_tags(
    paths: Iterable[str],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
    skill_name: str,
) -> List[str]:
    """The ``file:<path>`` tags that produced ``paths``.

    An operator debugging "why is my skill not published" needs the tag they
    wrote, not the archive path it became (§5.7).
    """
    wanted = set(paths)
    found: List[str] = []
    for obj in list(tools) + list(snippets):
        tag_path = extract_file_path_from_tags(obj.get("tags"))
        if tag_path and normalize_file_path(tag_path, skill_name) in wanted:
            found.append(f"file:{tag_path}")
    return sorted(set(found))


# --------------------------------------------------------------------------- #
# Artifact cache (§4.4, §5.8 #2)
# --------------------------------------------------------------------------- #
def cache_key(
    skill: Dict[str, Any],
    tools: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
) -> Optional[Tuple[Any, ...]]:
    """A freshness key over everything a skill's bytes depend on.

    ``modified_at`` already exists on every manifest, so invalidation is a
    string compare over ``DictCache`` reads — in memory, no disk I/O. A skill's
    bytes also depend on its tools and snippets, so those are in the key too.

    Returns ``None`` when any timestamp is missing: an imported or hand-placed
    object can carry ``modified_at: None``, and a ``None`` that never changes
    would mean the entry is cached once and **never** rebuilt (§5.8 #3). Treat
    it as always stale instead.
    """
    stamps = [skill.get("modified_at")]
    stamps.extend(t.get("modified_at") for t in tools)
    stamps.extend(s.get("modified_at") for s in snippets)
    if any(not isinstance(s, str) or not s for s in stamps):
        return None
    return (
        str(skill.get("modified_at")),
        tuple(sorted(str(t.get("modified_at")) for t in tools)),
        tuple(sorted(str(s.get("modified_at")) for s in snippets)),
    )


class ArtifactCache:
    """Bounded, content-addressed cache of built archives.

    Two lookups, for two jobs:

    * :meth:`current` — "is the build I would do right now already here?",
      keyed on the freshness key. This is the speed path.
    * :meth:`by_digest` — "give me the exact bytes with this digest", which is
      what closes the index/artifact race: the index publishes a digest at T1
      and the artifact is fetched at T2, so an edit in between would otherwise
      serve bytes that hash differently and the CLI would silently drop the
      skill (§5.14).

    Eviction is LRU over total payload bytes. A miss costs one export; a leak
    costs the process (§5.8 #2).
    """

    def __init__(self, max_bytes: int = DEFAULT_CACHE_MAX_BYTES):
        self.max_bytes = max_bytes
        self._by_digest: "OrderedDict[str, PublishedSkill]" = OrderedDict()
        self._fresh: Dict[Tuple[str, str], Tuple[Any, str]] = {}
        self._bytes = 0

    def current(
        self, uuid: str, slug: str, key: Optional[Tuple[Any, ...]]
    ) -> Optional[PublishedSkill]:
        if key is None:
            return None
        found = self._fresh.get((uuid, slug))
        if found is None or found[0] != key:
            return None
        return self.by_digest(uuid, slug, found[1])

    def by_digest(self, uuid: str, slug: str, digest: str) -> Optional[PublishedSkill]:
        entry = self._by_digest.get(digest)
        # Identity is re-checked so one skill's digest can never fetch another
        # skill's bytes, even though the digest alone is unique in practice.
        if entry is None or entry.uuid != uuid or entry.slug != slug:
            return None
        self._by_digest.move_to_end(digest)
        return entry

    def put(self, entry: PublishedSkill, key: Optional[Tuple[Any, ...]]) -> None:
        if entry.digest not in self._by_digest:
            self._by_digest[entry.digest] = entry
            self._bytes += len(entry.payload)
        self._by_digest.move_to_end(entry.digest)
        if key is not None:
            self._fresh[(entry.uuid, entry.slug)] = (key, entry.digest)
        self._evict()

    def _evict(self) -> None:
        while self._bytes > self.max_bytes and len(self._by_digest) > 1:
            digest, evicted = self._by_digest.popitem(last=False)
            self._bytes -= len(evicted.payload)
            stale = self._fresh.get((evicted.uuid, evicted.slug))
            if stale is not None and stale[1] == digest:
                self._fresh.pop((evicted.uuid, evicted.slug), None)

    # -- introspection, for tests and metrics ---------------------------- #
    def total_bytes(self) -> int:
        return self._bytes

    def size(self) -> int:
        return len(self._by_digest)

    def clear(self) -> None:
        self._by_digest.clear()
        self._fresh.clear()
        self._bytes = 0


_CACHE = ArtifactCache()


def get_cache() -> ArtifactCache:
    """The process-local artifact cache."""
    return _CACHE


# --------------------------------------------------------------------------- #
# Entry construction
# --------------------------------------------------------------------------- #
def build_entry(
    service: Any,
    uuid: str,
    slug: str,
    cache: Optional[ArtifactCache] = None,
) -> PublishedSkill:
    """Build (or recall) the published form of one skill.

    Composes the three archive fixes: the frontmatter carries the slug as its
    ``name`` (§5.8 #1), the paths are re-rooted so ``SKILL.md`` sits at the
    archive root (§5.2), and the bytes are deterministic so the published digest
    is the digest of what will actually be served (§5.1).

    Raises:
        KeyError: If the skill is not found.
        UnsafeArchivePathError: If a ``file:`` tag produced a path the CLI
            rejects.
        ArchiveTooLargeError: If the archive breaches a CLI cap.
    """
    cache = cache if cache is not None else _CACHE
    skill, tools, snippets, tool_modules = service.gather_export_inputs(uuid)
    key = cache_key(skill, tools, snippets)
    cached = cache.current(uuid, slug, key)
    if cached is not None:
        return cached

    # The original SBS name goes into `metadata` because the frontmatter `name`
    # is now the slug (§5.8 #1) and the two can differ — an agent, or a re-import,
    # would otherwise have no way back to what the store calls this skill.
    metadata: Dict[str, Any] = {}
    if str(skill["name"]) != slug:
        metadata["sbs_name"] = str(skill["name"])
    if is_internal(skill):
        metadata["internal"] = True

    files = _build_file_structure(
        skill,
        tools,
        snippets,
        tool_modules,
        name_override=slug,
        metadata=metadata or None,
    )
    files = strip_skill_prefix(files, str(skill["name"]))
    try:
        safe_archive_paths(files)
    except UnsafeArchivePathError as e:
        e.tags = offending_file_tags(e.paths, tools, snippets, str(skill["name"]))
        raise
    check_archive_limits(files)

    payload = build_deterministic_zip(files)
    entry = PublishedSkill(
        slug=slug,
        uuid=str(skill["uuid"]),
        name=str(skill["name"]),
        description=normalise_description(skill.get("description"), str(skill["name"])),
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )
    cache.put(entry, key)
    return entry


def publishable_entry(
    service: Any,
    uuid: str,
    slug: str,
    cache: Optional[ArtifactCache] = None,
) -> Optional[PublishedSkill]:
    """:func:`build_entry`, or ``None`` with a WARNING naming why.

    The per-skill publish predicate (§6.3 ``publishable``). Deliberately
    applies **no** lifecycle-state or tag gate: such a filter would be a second
    visibility rule the RBAC model does not express, and it would break the
    guarantee that npx installs exactly what the user can see (§4.3.1).
    """
    try:
        return build_entry(service, uuid, slug, cache=cache)
    except UnsafeArchivePathError as e:
        logger.warning(
            "Not publishing skill %r (%s) for npx: unsafe path(s) %s from tag(s) %s",
            slug,
            uuid,
            ", ".join(e.paths),
            ", ".join(getattr(e, "tags", [])) or "(unknown)",
        )
    except ArchiveTooLargeError as e:
        logger.warning("Not publishing skill %r (%s) for npx: %s", slug, uuid, e)
    except Exception as e:  # noqa: BLE001 — one broken skill must not 500 the index
        logger.warning("Not publishing skill %r (%s) for npx: %s", slug, uuid, e)
    return None


def build_index(entries: Iterable[PublishedSkill]) -> Dict[str, Any]:
    """A v0.2.0 discovery index over ``entries``."""
    return {
        "$schema": DISCOVERY_SCHEMA_V2,
        "skills": [e.index_entry() for e in entries],
    }


# --------------------------------------------------------------------------- #
# The install command — one definition, every surface
# --------------------------------------------------------------------------- #
def npx_install_url(base_url: str, ref: str) -> str:
    """The URL a user pastes after ``npx skills add``."""
    return f"{base_url.rstrip('/')}/pub/{ref}"


def npx_install_command(
    base_url: str,
    ref: str,
    agent: str = DEFAULT_AGENT,
) -> str:
    """The single definition of the install command, for every surface.

    ``GET /skills/{name}`` and ``GET /skills/`` both emit it per skill via the
    opt-in ``_npx_install`` field; the UI substitutes its own agent from the
    picker. Never assemble this string anywhere else — see docs/design/npx.md
    §4.3.5 and §4.3.8.
    """
    # `ref` is a scoped token (standalone) or the slug itself (disabled) — §6.4.
    # Always one skill per URL, so no -s and no -g: the single-entry index is
    # auto-selected, and a per-skill token is safe in a committed lockfile.
    #
    # Deliberately NO `DISABLE_TELEMETRY=1` prefix, resolving the one question
    # docs/design/npx.md left open (§9 Q7). The CLI reports a successful install
    # to add-skill.vercel.sh, and on a secured store `installUrl` is the
    # capability token (§1.8) — but prefixing the command we emit is the wrong
    # answer to that:
    #
    #   * it protects exactly one invocation. Every later `npx skills update`
    #     the user types themselves is unaffected, so the opt-out has to live in
    #     their environment to mean anything;
    #   * `VAR=1 cmd` is POSIX shell syntax, so the command we hand out would
    #     simply fail in PowerShell and cmd.exe;
    #   * `DO_NOT_TRACK=1`, exported once, is the cross-vendor convention and
    #     covers every run and every other CLI that honours it.
    #
    # So the command stays one clean portable line and the opt-out is documented
    # as a shell-profile setting (docs/cli.md). The UI says so next to the copy
    # button, where a reader about to paste a credential-bearing URL will
    # actually see it — prose at the right moment beats shell syntax they skim.
    return f"npx skills add {npx_install_url(base_url, ref)} -y -a {agent}"
