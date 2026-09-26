# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Prepare and serve per-deployment `sbs` CLI artifacts.

docs/design/new_cli.md §5.2–§5.4. The job: take the CI-built artifacts that ship
with (or are mounted into) the image, stamp this deployment's own public URL into
them, and publish a manifest describing what is available.

Two mechanisms, both measured:

``patch``
    Rewrite a fixed-width, ``#``-padded URL slot inside an already-linked binary,
    in place. Costs milliseconds, changes no bytes of length, and needs no
    compiler in the runtime image — which is why it is the default (§5.4 option
    A). Verified on Linux with a 0-byte size delta and a working binary (M9).

``rebuild``
    ``GOOS/GOARCH go build -ldflags -X main.urlSlot=<url>``. ~2.3 s per platform,
    needs a Go toolchain and the module cache, and is the only mechanism that
    covers ``darwin-arm64`` — Go's linker ad-hoc signs that target, so patching
    its bytes is expected to break execution (B3).

Everything here runs **off the request path**, in a background task started from
the lifespan hook. Failures downgrade one platform's ``state`` and are logged;
they never fail startup and ``/health/ready`` deliberately does not wait for them
(§5.3) — readiness means "can answer content requests".
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from skillberry_store.fast_api.platform_detect import SUPPORTED_PLATFORMS

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# The URL slot
# --------------------------------------------------------------------------- #
# Must match cli/go/version.go exactly. The Go side declares
#
#     var urlSlot = "http://localhost:8000#######################################"
#     const slotWidth = 60
#
# and this module locates that byte sequence in a linked binary and overwrites
# it. The two constants are a contract between a Python writer and a Go reader,
# so `test_cli_artifacts.py` reads them back out of the Go source and asserts
# they agree — a silent drift here produces a binary that points at the wrong
# store, which is the single worst failure this feature can have.
SLOT_WIDTH = 60
SLOT_PAD = b"#"
SLOT_DEFAULT_URL = b"http://localhost:8000"
SLOT_PATTERN = SLOT_DEFAULT_URL + SLOT_PAD * (SLOT_WIDTH - len(SLOT_DEFAULT_URL))

# §5.7 / §7.2 / B14. Identical to the Go and shell validators. A URL from this
# grammar cannot carry a quote, a backtick, a dollar, a semicolon, a pipe or
# whitespace, which is what makes it safe to inline into a generated shell script
# and to pass as an -ldflags value.
URL_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?$")

# State values in the served manifest (§5.5.1).
STATE_READY = "ready"
STATE_PREPARING = "preparing"
STATE_UNAVAILABLE = "unavailable"

# Reasons a platform can be unavailable. Surfaced verbatim to the UI and the CLI,
# so each has to mean something to someone reading it in a terminal.
REASON_NOT_BUNDLED = "not_bundled"
REASON_PREPARE_FAILED = "prepare_failed"
REASON_URL_TOO_LONG = "url_too_long"
REASON_NO_SLOT = "no_slot_found"
REASON_NO_TOOLCHAIN = "no_toolchain"

# Mechanisms recorded per artifact.
MECHANISM_PATCH = "patch"
MECHANISM_REBUILD = "rebuild"
MECHANISM_SIDECAR = "sidecar"
MECHANISM_PRISTINE = "pristine"


class UnacceptableURL(ValueError):
    """A URL that must never reach a linker flag, a script or a binary."""


def validate_public_url(url: str, *, for_slot: bool = False) -> str:
    """Validate and normalise a URL before it is baked, patched or inlined.

    ``for_slot`` additionally enforces the fixed slot width, which only applies
    to a value being written into a binary.

    This is the single choke point named in §7.2. A value like
    ``evil.com/"$(id)"`` reaching a generated shell script is command execution
    on the user's machine, and reaching an ``-ldflags`` value is argument
    injection into our own build. Both are prevented here rather than by quoting
    at each use site, because a use site that forgets is silent.
    """
    if not url or not isinstance(url, str):
        raise UnacceptableURL("URL is empty")
    candidate = url.strip().rstrip("/")
    if not URL_RE.match(candidate):
        raise UnacceptableURL(f"{url!r} is not an acceptable http(s) URL")
    if for_slot and len(candidate.encode()) > SLOT_WIDTH:
        raise UnacceptableURL(
            f"{candidate!r} is {len(candidate.encode())} bytes, which exceeds the "
            f"{SLOT_WIDTH}-byte slot"
        )
    return candidate


def sha256_file(path: Path, *, chunk: int = 1 << 20) -> str:
    """Streaming sha256 — these files are ~32 MB, so never read one whole."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Slot patching
# --------------------------------------------------------------------------- #


def patch_url_slot(data: bytearray, url: str) -> int:
    """Overwrite every copy of the URL slot in ``data``. Returns the count.

    Operates on a mutable buffer rather than a file so the caller controls
    atomicity, and so this is trivially testable.

    Two invariants, both asserted rather than assumed:

    * The replacement is exactly ``SLOT_WIDTH`` bytes, so the file size cannot
      change. A linked binary has offsets and, on some formats, checksums that a
      length change would invalidate.
    * The padding byte cannot appear in a URL that passes ``validate_public_url``,
      so trimming it on the Go side can never truncate a real URL.
    """
    encoded = validate_public_url(url, for_slot=True).encode()
    replacement = encoded + SLOT_PAD * (SLOT_WIDTH - len(encoded))
    if len(replacement) != SLOT_WIDTH:  # pragma: no cover - arithmetic guard
        raise UnacceptableURL(
            f"internal error: replacement is {len(replacement)} bytes, want {SLOT_WIDTH}"
        )

    count = 0
    start = 0
    while True:
        offset = data.find(SLOT_PATTERN, start)
        if offset < 0:
            break
        data[offset : offset + SLOT_WIDTH] = replacement
        count += 1
        start = offset + SLOT_WIDTH
    return count


# --------------------------------------------------------------------------- #
# Manifest records
# --------------------------------------------------------------------------- #


@dataclass
class PlatformArtifact:
    """One platform's entry in the served manifest."""

    platform: str
    state: str = STATE_UNAVAILABLE
    filename: str = ""
    size: int = 0
    sha256: str = ""
    url_injection: str = ""
    reason: str = ""
    stamp: str = ""
    archive_filename: str = ""
    archive_size: int = 0
    archive_sha256: str = ""

    def to_manifest(self) -> dict:
        """Render for /cli/manifest.

        URLs are **relative** so the document stays correct behind any path
        prefix or ingress rewrite, and so one document can feed the UI, the CLI
        and the install scripts without the server having to know its own
        external address (§5.5.1).
        """
        entry: dict = {"state": self.state}
        if self.state == STATE_READY:
            entry.update(
                {
                    "filename": self.filename,
                    "size": self.size,
                    "sha256": self.sha256,
                    "url_injection": self.url_injection,
                    "download_url": (
                        f"/cli/download?platform={self.platform}&format=raw"
                    ),
                    "archive_url": (
                        f"/cli/download?platform={self.platform}&format=archive"
                    ),
                }
            )
            if self.archive_sha256:
                entry["archive_sha256"] = self.archive_sha256
                entry["archive_size"] = self.archive_size
                entry["archive_filename"] = self.archive_filename
        else:
            if self.reason:
                entry["reason"] = self.reason
            if self.state == STATE_PREPARING:
                entry["retry_after"] = 10
        return entry


@dataclass
class CliArtifactSettings:
    """The §6 configuration surface for this service."""

    enabled: bool = True
    prepare: str = "auto"  # auto | always | never
    build_mode: str = "patch"  # patch | rebuild | auto
    dist_dir: Path = field(default_factory=lambda: Path("cli-dist"))
    artifacts_dir: Path = field(default_factory=lambda: Path("/app/cli-prebuilt"))
    artifacts_url: Optional[str] = None
    max_concurrent_downloads: int = 8

    @classmethod
    def from_env(cls, base_dir: Optional[str] = None) -> "CliArtifactSettings":
        """Read the SBS_CLI_* variables.

        Read from the environment here rather than added to ``SBSettings``
        because these are a self-contained group that only this service consumes,
        and because ``dist_dir``'s default depends on ``SBS_BASE_DIR`` — a
        derivation that reads more clearly as a line of code than as a pydantic
        validator.
        """
        base = base_dir or os.environ.get("SBS_BASE_DIR") or "."
        dist_default = Path(base) / "cli-dist"
        return cls(
            enabled=_env_flag("SBS_CLI_DOWNLOAD", default=True),
            prepare=_env_choice("SBS_CLI_PREPARE", ("auto", "always", "never"), "auto"),
            build_mode=_env_choice(
                "SBS_CLI_BUILD_MODE", ("patch", "rebuild", "auto"), "patch"
            ),
            dist_dir=Path(os.environ.get("SBS_CLI_DIST_DIR") or dist_default),
            artifacts_dir=Path(
                os.environ.get("SBS_CLI_ARTIFACTS_DIR") or "/app/cli-prebuilt"
            ),
            artifacts_url=(os.environ.get("SBS_CLI_ARTIFACTS_URL") or None),
            max_concurrent_downloads=_env_int("SBS_CLI_MAX_CONCURRENT_DOWNLOADS", 8),
        )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    # `off` is the value §6 documents for SBS_CLI_DOWNLOAD; the rest are accepted
    # because an operator who writes `false` or `0` obviously means the same.
    return raw.strip().lower() not in ("off", "false", "0", "no", "")


def _env_choice(name: str, allowed: Iterable[str], default: str) -> str:
    raw = (os.environ.get(name) or "").strip().lower()
    allowed = tuple(allowed)
    if raw in allowed:
        return raw
    if raw:
        logger.warning(
            "%s=%r is not one of %s; using %r", name, raw, list(allowed), default
        )
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    if value < 1:
        logger.warning("%s=%d must be >= 1; using %d", name, value, default)
        return default
    return value


# --------------------------------------------------------------------------- #
# The service
# --------------------------------------------------------------------------- #


class CliArtifactService:
    """Owns the prepared-artifact directory and the manifest that describes it.

    Lifecycle:

    * ``__init__`` is cheap and does no I/O, so constructing it in ``SBS.__init__``
      cannot slow startup or fail it.
    * ``load_manifest`` reads whatever a previous run (or another replica) left
      behind, so downloads can be served immediately on a warm dist dir.
    * ``prepare_all`` is the background task. It is the only method that writes.
    * ``resolve`` answers a download request from in-memory state.
    """

    #: Marker file recording the inputs a prepared artifact was made from.
    MANIFEST_NAME = "manifest.json"

    def __init__(
        self,
        settings: CliArtifactSettings,
        *,
        public_url: Optional[str] = None,
        cli_commit: str = "unknown",
    ):
        self.settings = settings
        self.public_url = public_url
        self.cli_commit = cli_commit
        self._platforms: dict[str, PlatformArtifact] = {
            platform: PlatformArtifact(platform=platform, reason=REASON_NOT_BUNDLED)
            for platform in SUPPORTED_PLATFORMS
        }
        self._cli_version = "unknown"
        self._engine_version = "unknown"
        self._generated_at: Optional[str] = None
        self._prepared = False

    # -- properties -------------------------------------------------------- #

    @property
    def dist_dir(self) -> Path:
        return self.settings.dist_dir

    @property
    def cli_version(self) -> str:
        return self._cli_version

    @property
    def engine_version(self) -> str:
        return self._engine_version

    # -- stamping ---------------------------------------------------------- #

    def stamp_key(self, platform: str, mechanism: str) -> str:
        """The §5.3 preparation stamp.

        ``sha256(public_url ‖ cli_commit ‖ engine_version ‖ platform ‖ mechanism)``

        Every input is something that, if changed, makes the prepared artifact
        wrong rather than merely stale:

        * ``public_url`` — the whole point; a changed URL means a re-prepare.
        * ``cli_commit`` — new CLI code means a new artifact.
        * ``engine_version`` — a restish upgrade changes behaviour users see.
        * ``platform`` — separate artifacts, separate stamps.
        * ``mechanism`` — a ``patch`` artifact and a ``rebuild`` artifact for the
          same URL are different bytes, and the manifest advertises which.

        The stamp is checked *together with* the file's recorded size and sha256
        (see ``_is_current``), so a corrupt or truncated file re-prepares even
        when every input is unchanged.
        """
        material = "\u0000".join(
            [
                self.public_url or "",
                self.cli_commit,
                self._engine_version,
                platform,
                mechanism,
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _is_current(self, entry: PlatformArtifact, mechanism: str) -> bool:
        """Whether ``entry`` can be reused without doing any work."""
        if entry.state != STATE_READY or not entry.stamp:
            return False
        if entry.stamp != self.stamp_key(entry.platform, mechanism):
            return False
        path = self.artifact_path(entry.platform, entry.filename)
        if not path.is_file():
            return False
        try:
            if path.stat().st_size != entry.size:
                return False
        except OSError:
            return False
        # The sha256 re-check is what turns "the stamp matches" into "the file is
        # actually the one the stamp describes". It costs ~30 ms for 32 MB and
        # runs once per platform at startup, which is a fair price for not
        # serving a half-written artifact after an unclean shutdown.
        return sha256_file(path) == entry.sha256

    # -- paths ------------------------------------------------------------- #

    def artifact_path(self, platform: str, filename: str = "") -> Path:
        """Where a prepared artifact lives.

        ``platform`` is always a member of the closed ``SUPPORTED_PLATFORMS``
        enum by the time it reaches here — callers validate first — so this join
        cannot be a traversal (B13, §7.2). The belt-and-braces check below makes
        that a property of this function rather than of every caller.
        """
        if platform not in SUPPORTED_PLATFORMS:
            raise ValueError(f"unknown platform {platform!r}")
        name = filename or self.default_filename(platform)
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise ValueError(f"unsafe artifact filename {name!r}")
        return self.dist_dir / platform / name

    @staticmethod
    def default_filename(platform: str) -> str:
        return "sbs.exe" if platform.startswith("windows-") else "sbs"

    @staticmethod
    def archive_name(platform: str) -> str:
        suffix = ".zip" if platform.startswith("windows-") else ".tar.gz"
        return f"sbs-{platform}{suffix}"

    def archive_path(self, platform: str) -> Path:
        return self.dist_dir / platform / self.archive_name(platform)

    # -- manifest I/O ------------------------------------------------------ #

    def manifest_path(self) -> Path:
        return self.dist_dir / self.MANIFEST_NAME

    def load_manifest(self) -> None:
        """Adopt a manifest a previous run or another replica wrote.

        Best-effort by design: the dist dir is a *cache* (§5.3, B6). A missing,
        truncated or unparseable manifest means "nothing prepared yet", which
        costs one background re-preparation and never an error to a user.
        """
        path = self.manifest_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return

        # A manifest prepared for a *different* public URL must not be adopted:
        # its artifacts point somewhere else. Discarding it here is what makes a
        # changed SBS_PUBLIC_URL re-prepare rather than silently serve stale
        # binaries — the stamp check would catch it too, but only after this
        # method had already advertised them as ready.
        if raw.get("public_url") and raw.get("public_url") != self.public_url:
            logger.info(
                "Ignoring prepared CLI manifest for %s; this deployment serves %s",
                raw.get("public_url"),
                self.public_url,
            )
            return

        self._cli_version = raw.get("cli_version") or self._cli_version
        engine = raw.get("engine") or {}
        if isinstance(engine, dict) and engine.get("version"):
            self._engine_version = engine["version"]
        self._generated_at = raw.get("generated_at")

        for platform, entry in (raw.get("platforms") or {}).items():
            if platform not in self._platforms or not isinstance(entry, dict):
                continue
            self._platforms[platform] = PlatformArtifact(
                platform=platform,
                state=entry.get("state", STATE_UNAVAILABLE),
                filename=entry.get("filename", ""),
                size=int(entry.get("size") or 0),
                sha256=entry.get("sha256", ""),
                url_injection=entry.get("url_injection", ""),
                reason=entry.get("reason", ""),
                stamp=entry.get("stamp", ""),
                archive_filename=entry.get("archive_filename", ""),
                archive_size=int(entry.get("archive_size") or 0),
                archive_sha256=entry.get("archive_sha256", ""),
            )

    def manifest(self) -> dict:
        """The served /cli/manifest document (§5.5.1)."""
        return {
            "cli_name": "sbs",
            "cli_version": self._cli_version,
            "public_url": self.public_url,
            "generated_at": self._generated_at,
            "engine": {
                "name": "restish",
                "version": self._engine_version,
                "license": "MIT",
                "license_url": "/cli/license",
            },
            "platforms": {
                platform: entry.to_manifest()
                for platform, entry in sorted(self._platforms.items())
            },
        }

    def _write_manifest(self) -> None:
        """Write the manifest last, atomically (§5.3, B7).

        Last, because it is what advertises the artifacts: a manifest that named
        a file still being written would let a reader download a truncated
        binary. Atomically, because two replicas can each prepare their own copy
        and a torn manifest would make both unreadable. The ETag is the content
        sha256, so independent copies still agree.
        """
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        doc = self.manifest()
        # The stamp is persisted but deliberately NOT part of the served
        # document: it is an internal cache key, and publishing it would invite
        # clients to depend on its shape.
        for platform, entry in self._platforms.items():
            if entry.stamp:
                doc["platforms"][platform]["stamp"] = entry.stamp

        target = self.manifest_path()
        fd, tmp_name = tempfile.mkstemp(dir=str(self.dist_dir), prefix=".manifest-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, target)
        except BaseException:
            # Leaving a .manifest-* turd behind would accumulate on every failure.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- resolution (request path) ----------------------------------------- #

    def resolve(self, platform: str) -> PlatformArtifact:
        """The in-memory entry for a platform. No I/O — this is on the hot path."""
        return self._platforms.get(
            platform, PlatformArtifact(platform=platform, reason=REASON_NOT_BUNDLED)
        )

    def mark_preparing(self) -> None:
        """Flip not-yet-ready platforms to `preparing` before the work starts.

        So a client polling during startup gets `503 + Retry-After` and a
        "being prepared" message, rather than `unavailable` — which reads as
        "never coming" and would send a user looking for a different install
        method (§5.5.1, §5.8 #2).
        """
        for entry in self._platforms.values():
            if entry.state != STATE_READY:
                entry.state = STATE_PREPARING
                entry.reason = ""

    # -- preparation (background) ------------------------------------------ #

    def prepare_all(self) -> None:
        """Prepare an artifact per platform for this deployment's public URL.

        This is the background task of §5.3. It is synchronous and blocking by
        design — the caller runs it in a thread executor — because the work is
        file I/O and subprocesses, neither of which benefits from async.

        Every failure mode degrades one platform and is logged. Nothing raised
        from here should reach the caller, because the caller is a fire-and-forget
        task whose failure would be an unhandled exception in the event loop.
        """
        if not self.settings.enabled:
            logger.info("SBS_CLI_DOWNLOAD is off; not preparing CLI artifacts")
            return
        if self.settings.prepare == "never":
            logger.info("SBS_CLI_PREPARE=never; serving whatever is already prepared")
            return

        if not self.public_url:
            # Without a public URL there is nothing to inject. Serving the
            # CI-built artifacts pristine is the truthful outcome: they carry
            # their compile-time default, the manifest says `pristine`, and the
            # install script (which derives the URL per request) still works.
            logger.warning(
                "SBS_PUBLIC_URL is not set; serving pristine CLI artifacts that "
                "carry their compiled-in default URL. Downloaded binaries will "
                "need `sbs connect <url>`."
            )

        started = time.monotonic()
        source_dir = self.settings.artifacts_dir
        self._read_prebuilt_metadata(source_dir)

        if not source_dir.is_dir():
            logger.warning(
                "No CLI artifacts at %s; every platform will report %r. Build them "
                "with `make cli-dist`, mount them, or set SBS_CLI_ARTIFACTS_DIR.",
                source_dir,
                REASON_NOT_BUNDLED,
            )
            for entry in self._platforms.values():
                if entry.state != STATE_READY:
                    entry.state = STATE_UNAVAILABLE
                    entry.reason = REASON_NOT_BUNDLED
            self._safe_write_manifest()
            return

        # One inter-process lock around the whole dist dir (§5.3, B7). Two
        # workers or two replicas sharing a volume must not interleave writes to
        # the same artifact, and the lock is cheaper than making every individual
        # write safe against a concurrent writer.
        lock = self._dist_lock()
        acquired = lock.acquire(timeout=120) if lock else True
        if not acquired:
            logger.warning(
                "Could not acquire the CLI dist lock within 120s; another process "
                "is preparing artifacts. Skipping this run."
            )
            return
        try:
            for platform in SUPPORTED_PLATFORMS:
                try:
                    self._prepare_platform(platform, source_dir)
                except Exception:
                    # One platform's failure must not abandon the others.
                    logger.exception("Preparing the %s CLI artifact failed", platform)
                    entry = self._platforms[platform]
                    entry.state = STATE_UNAVAILABLE
                    entry.reason = REASON_PREPARE_FAILED
            self._safe_write_manifest()
        finally:
            if lock:
                try:
                    lock.release()
                except Exception:  # pragma: no cover - lock teardown
                    logger.debug("Releasing the CLI dist lock failed", exc_info=True)

        self._prepared = True
        ready = sorted(p for p, e in self._platforms.items() if e.state == STATE_READY)
        logger.info(
            "CLI artifact preparation finished in %.2fs: %d/%d ready (%s)",
            time.monotonic() - started,
            len(ready),
            len(SUPPORTED_PLATFORMS),
            ", ".join(ready) if ready else "none",
        )

    def _dist_lock(self):
        """An inter-process lock file beside the dist dir.

        Returns None when fasteners is unavailable rather than failing: a
        single-process deployment does not need it, and the degradation
        (a possible duplicated preparation) is harmless because each write is
        atomic anyway.
        """
        try:
            import fasteners
        except ImportError:  # pragma: no cover - fasteners is a declared dep
            return None
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        return fasteners.InterProcessLock(str(self.dist_dir / ".prepare.lock"))

    def _safe_write_manifest(self) -> None:
        # Stamped at write time rather than at construction: the field answers
        # "when were these artifacts prepared?", which is what a support request
        # or a stale-cache investigation actually asks. Setting it in __init__
        # would report process start and be wrong for an adopted manifest.
        self._generated_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        try:
            self._write_manifest()
        except OSError:
            # A read-only dist dir is a misconfiguration worth logging, but the
            # in-memory manifest is still correct and still served.
            logger.exception("Could not write the CLI manifest to %s", self.dist_dir)

    def _read_prebuilt_metadata(self, source_dir: Path) -> None:
        """Adopt cli_version and engine version from the CI build's manifest.

        These are inputs to the stamp key, so they have to come from the artifacts
        themselves rather than from the running server's own version — the image
        can carry artifacts built from a different commit than the Python code.
        """
        path = source_dir / "prebuilt-manifest.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        self._cli_version = raw.get("cli_version") or self._cli_version
        engine = raw.get("engine") or {}
        if isinstance(engine, dict) and engine.get("version"):
            self._engine_version = engine["version"]

    def _source_artifact(self, source_dir: Path, platform: str) -> Optional[Path]:
        """Locate the CI-built artifact for a platform.

        Two layouts are accepted: ``<dir>/<platform>/sbs`` (what cli/build.sh
        emits) and a flat ``<dir>/sbs-<platform>``, because a release-asset
        download naturally produces the latter and making operators rename files
        would be a pointless obstacle.
        """
        filename = self.default_filename(platform)
        candidates = [
            source_dir / platform / filename,
            source_dir / f"sbs-{platform}" / filename,
            source_dir / f"sbs-{platform}",
            source_dir / f"sbs-{platform}.exe",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _choose_mechanism(self, platform: str) -> str:
        """Which injection mechanism applies to this platform (§5.2).

        The table this implements:

        ==================  ================  ==================
        Platform            Toolchain         No toolchain
        ==================  ================  ==================
        linux-*             rebuild           patch (verified)
        windows-amd64       rebuild           patch
        darwin-amd64        rebuild           patch
        darwin-arm64        rebuild           **sidecar**
        ==================  ================  ==================

        ``darwin-arm64`` is the one cell that cannot be patched: Go's linker
        ad-hoc signs that target, so rewriting bytes inside the signed image
        invalidates the signature and macOS refuses to exec it (B3). A sidecar
        file carrying the URL is the honest fallback, and a real ``rebuild``
        removes the caveat entirely.
        """
        if not self.public_url:
            return MECHANISM_PRISTINE

        mode = self.settings.build_mode
        if mode == MECHANISM_REBUILD or (mode == "auto" and self._have_toolchain()):
            return MECHANISM_REBUILD
        if platform == "darwin-arm64":
            return MECHANISM_SIDECAR
        return MECHANISM_PATCH

    @staticmethod
    def _have_toolchain() -> bool:
        return shutil.which("go") is not None

    def _prepare_platform(self, platform: str, source_dir: Path) -> None:
        """Prepare one platform's artifact, or record why it could not be."""
        entry = self._platforms[platform]
        mechanism = self._choose_mechanism(platform)

        # The stamp check (§5.3): unchanged inputs means literally zero work,
        # which is what makes a restart with an unchanged SBS_PUBLIC_URL free.
        if self.settings.prepare != "always" and self._is_current(entry, mechanism):
            logger.debug("CLI artifact for %s is already current; skipping", platform)
            return

        source = self._source_artifact(source_dir, platform)
        if source is None:
            entry.state = STATE_UNAVAILABLE
            entry.reason = REASON_NOT_BUNDLED
            logger.info(
                "No prebuilt CLI artifact for %s under %s", platform, source_dir
            )
            return

        filename = self.default_filename(platform)
        target = self.artifact_path(platform, filename)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            if mechanism == MECHANISM_REBUILD:
                self._rebuild(platform, target)
            else:
                self._copy_and_maybe_patch(platform, source, target, mechanism)
        except UnacceptableURL as exc:
            entry.state = STATE_UNAVAILABLE
            entry.reason = (
                REASON_URL_TOO_LONG if "exceeds" in str(exc) else REASON_PREPARE_FAILED
            )
            logger.error("Refusing to prepare %s: %s", platform, exc)
            return
        except _NoSlotFound:
            # A CI artifact with no slot is a build/version mismatch: the Python
            # SLOT_PATTERN and the Go urlSlot have drifted. Serving it pristine
            # would point users at the wrong store, so refuse instead.
            entry.state = STATE_UNAVAILABLE
            entry.reason = REASON_NO_SLOT
            logger.error(
                "The %s artifact contains no URL slot. The prebuilt binary and "
                "this server disagree about cli/go/version.go's urlSlot. Rebuild "
                "the artifacts with `make cli-dist`.",
                platform,
            )
            return
        except _NoToolchain:
            entry.state = STATE_UNAVAILABLE
            entry.reason = REASON_NO_TOOLCHAIN
            logger.error(
                "SBS_CLI_BUILD_MODE=rebuild needs a Go toolchain, which this image "
                "does not have. Use the default `patch` mode, or the "
                "-cli-builder image variant."
            )
            return

        sidecar_note = ""
        if mechanism == MECHANISM_SIDECAR:
            # The URL lives beside the binary rather than inside it, so the
            # archive is the only complete download for this platform.
            self._write_sidecar(platform)
            sidecar_note = " (URL in a sidecar file; use format=archive)"

        # sha256 is computed AFTER patching (§7.2), so the manifest describes the
        # bytes actually served rather than the bytes CI produced.
        entry.filename = filename
        entry.size = target.stat().st_size
        entry.sha256 = sha256_file(target)
        entry.url_injection = mechanism
        entry.stamp = self.stamp_key(platform, mechanism)
        entry.state = STATE_READY
        entry.reason = ""

        self._build_archive(platform, entry)

        logger.info(
            "Prepared the %s CLI artifact via %s: %d bytes, sha256 %s%s",
            platform,
            mechanism,
            entry.size,
            entry.sha256[:12],
            sidecar_note,
        )

    def _copy_and_maybe_patch(
        self, platform: str, source: Path, target: Path, mechanism: str
    ) -> None:
        """Copy the CI artifact into the dist dir, patching the slot if asked.

        Written through a temp file in the destination directory and renamed, so a
        reader can never observe a partially written binary and a crash cannot
        leave one behind (§5.3).
        """
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".sbs-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if mechanism == MECHANISM_PATCH:
                data = bytearray(source.read_bytes())
                original_size = len(data)
                count = patch_url_slot(data, self.public_url or "")
                if count == 0:
                    raise _NoSlotFound(platform)
                if len(data) != original_size:  # pragma: no cover - width guard
                    raise _NoSlotFound(platform)
                tmp.write_bytes(bytes(data))
            else:
                # `pristine` and `sidecar` both ship the CI bytes unchanged.
                shutil.copyfile(source, tmp)
            # 0755: this is an executable, and a download that loses the bit is
            # the exact problem the raw-format path exists to avoid.
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _rebuild(self, platform: str, target: Path) -> None:
        """Cross-compile with the URL baked in via -ldflags (§5.2 `rebuild`)."""
        if not self._have_toolchain():
            raise _NoToolchain(platform)

        cli_dir = Path(__file__).resolve().parents[3] / "cli" / "go"
        if not (cli_dir / "go.mod").is_file():
            raise _NoToolchain(platform)

        url = validate_public_url(self.public_url or "", for_slot=True)
        goos, _, goarch = platform.partition("-")

        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".sbs-build-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            ldflags = (
                f"-s -w -X main.urlSlot={url} "
                f"-X main.version={self._cli_version} "
                f"-X main.engineVersion={self._engine_version}"
            )
            env = {
                **os.environ,
                "CGO_ENABLED": "0",
                "GOOS": goos,
                "GOARCH": goarch,
                # A build triggered by a server start must never reach the
                # network for dependencies: it would make startup depend on
                # proxy.golang.org, and an air-gapped image would hang. Vendored
                # deps (`make cli-vendor`) or a warm module cache are required.
                "GOFLAGS": os.environ.get("GOFLAGS", ""),
            }
            # argv list, never a shell (§7.2, B14): `url` is validated above, but
            # exec-without-a-shell is what makes that validation a second line of
            # defence rather than the only one.
            proc = subprocess.run(
                ["go", "build", "-trimpath", "-ldflags", ldflags, "-o", str(tmp), "."],
                cwd=str(cli_dir),
                capture_output=True,
                text=True,
                env=env,
                timeout=600,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"go build for {platform} failed: {proc.stderr.strip()[:500]}"
                )
            os.chmod(tmp, 0o755)
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def _write_sidecar(self, platform: str) -> None:
        """Write `sbs.url` beside a sidecar-mechanism binary (§5.2).

        One line, no trailing structure: the CLI reads it on first run and folds
        it into its config. Simple on purpose — this file is parsed by a binary
        that may be the only thing the user has.
        """
        path = self.dist_dir / platform / "sbs.url"
        path.write_text(f"{self.public_url}\n", encoding="utf-8")

    def _build_archive(self, platform: str, entry: PlatformArtifact) -> None:
        """Build the browser-download archive (§5.5.2).

        `format=archive` is the browser default for three concrete reasons, not
        tidiness: a browser download loses the executable bit, macOS stamps it
        `com.apple.quarantine`, and the sidecar mechanism needs a second file to
        arrive alongside the first.

        Contents: the binary, `LICENSE.restish` (we redistribute an MIT binary),
        and `sbs.url` where the mechanism calls for it.

        # Why the archive is built deterministically

        Every field that would otherwise vary run to run — gzip's embedded mtime,
        each entry's mtime, uid/gid and uname/gname — is pinned. Without that the
        archive bytes differ on every preparation, and §5.3's guarantee that "two
        replicas that each prepared their own copy still agree" holds for the raw
        binary (patching is deterministic) but **not** for the archive.

        That is not cosmetic. A client behind a load balancer can read the
        manifest from replica A and fetch the archive from replica B; the
        `archive_sha256` then mismatches, and both the install script and
        `sbs download-cli` are built to *refuse* on a checksum mismatch. The
        result would be an intermittent, unreproducible "checksum mismatch —
        refusing to install" that looks exactly like an attack. Observed while
        testing: restarting the store changed every `archive_sha256`.
        """
        binary = self.artifact_path(platform, entry.filename)
        archive = self.archive_path(platform)
        license_path = self.settings.artifacts_dir / "LICENSE.restish"
        sidecar = self.dist_dir / platform / "sbs.url"

        members = [(binary, entry.filename, 0o755)]
        for path, arcname in (
            (license_path, "LICENSE.restish"),
            (sidecar, "sbs.url"),
        ):
            if path.is_file():
                members.append((path, arcname, 0o644))

        fd, tmp_name = tempfile.mkstemp(dir=str(archive.parent), prefix=".sbs-arc-")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            if platform.startswith("windows-"):
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                    for path, arcname, mode in members:
                        # A fixed date_time: ZipInfo.from_file would embed the
                        # staged file's mtime. 1980-01-01 is the earliest the ZIP
                        # format can represent.
                        info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
                        # The high 16 bits carry the POSIX mode; 0o100000 marks a
                        # regular file. Without this, unzip on a POSIX host gives
                        # the binary no executable bit.
                        info.external_attr = (0o100000 | mode) << 16
                        info.compress_type = zipfile.ZIP_DEFLATED
                        zf.writestr(info, path.read_bytes())
            else:
                # gzip's own header carries an mtime, which tarfile.open("w:gz")
                # fills from the clock. Wrapping an explicit GzipFile(mtime=0) is
                # the only way to pin it.
                with open(tmp, "wb") as raw:
                    with gzip.GzipFile(
                        filename="", mode="wb", fileobj=raw, mtime=0
                    ) as gz:
                        with tarfile.open(fileobj=gz, mode="w") as tf:
                            for path, arcname, mode in members:
                                info = tarfile.TarInfo(name=arcname)
                                info.size = path.stat().st_size
                                info.mode = mode
                                info.mtime = 0
                                info.type = tarfile.REGTYPE
                                # Numeric and name ownership both pinned: the
                                # server's own uid/gid are irrelevant to the user
                                # extracting this, and they vary per deployment.
                                info.uid = 0
                                info.gid = 0
                                info.uname = ""
                                info.gname = ""
                                with path.open("rb") as fh:
                                    tf.addfile(info, fh)
            os.chmod(tmp, 0o644)
            os.replace(tmp, archive)
        except BaseException:
            tmp.unlink(missing_ok=True)
            logger.exception("Building the %s download archive failed", platform)
            return

        entry.archive_filename = archive.name
        entry.archive_size = archive.stat().st_size
        entry.archive_sha256 = sha256_file(archive)


class _NoSlotFound(RuntimeError):
    """The prebuilt artifact contains no patchable URL slot."""


class _NoToolchain(RuntimeError):
    """`rebuild` was requested but no Go toolchain is present."""


def artifact_checksums(service: "CliArtifactService") -> dict[str, str]:
    """Per-platform sha256 of the *raw* artifact, for inlining into a script.

    Only ready platforms appear. A platform that is missing from the mapping makes
    the generated script warn rather than refuse — at that point the download it
    is verifying would already have failed.
    """
    return {
        platform: entry.sha256
        for platform, entry in service._platforms.items()
        if entry.state == STATE_READY and entry.sha256
    }
