# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Scoped publish tokens for ``npx skills add`` (docs/design/npx.md §4.3.3/§4.3.7).

The npx CLI sends no ``Authorization`` header, cookie or credential to a
well-known host (§1.4), so a store behind auth cannot be installed from at all
unless the capability travels in the URL. This module derives that capability.

Three properties are load-bearing, and each of them is a requirement to enforce
rather than something that falls out for free:

* **Derived, not minted.** A publish token is ``HMAC(secret, "tenant|scope")``,
  not a row in a table. The URL lives in the user's ``skills-lock.json`` and is
  replayed by every ``npx skills update``, so it must survive a restart; there
  are no mint / list / revoke endpoints to write, and the only durable state is
  one secret.
* **Never the session token.** A session token is in-memory, expires in 12 h and
  carries its holder's full RBAC. Deriving from it would break the URL on every
  login and every restart, and putting it in a URL would send admin privileges
  to a shell history, a lockfile and Vercel's telemetry endpoint (§1.8). A
  session proves who you are so you may *learn* the URL; it is never in it.
* **Never resolvable as a bearer.** The token is accepted as a path segment by
  the well-known handler and nowhere else. It must not be derived from, or
  resolved through, ``SessionStore`` — that would make it a general-purpose
  credential with the full RBAC of its tenant, which is the exact leak this
  design exists to prevent (§4.3.4).

Scopes (§4.3.7): ``skill:<slug>`` (the default, and what the UI emits),
``ns:<name>`` and ``*``. A leaked URL grants exactly what its holder was given
— for a per-skill token in a committed lockfile, content that is already in the
repository beside it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

SECRET_ENV_VAR = "SBS_WELLKNOWN_SECRET"
SECRET_FILE_ENV_VAR = "SBS_WELLKNOWN_SECRET_FILE"

#: base64url of a full 32-byte HMAC digest with ``=`` stripped — 43 characters
#: (§5.10 #5). Not a truncated hex string: there is no reason to spend entropy.
TOKEN_LENGTH = 43

SCOPE_ALL = "*"


def skill_scope(slug: str) -> str:
    """The scope naming exactly one skill."""
    return f"skill:{slug}"


def namespace_scope(namespace: str) -> str:
    """The scope naming one namespace (the "pack" analogue, §6.7)."""
    return f"ns:{namespace}"


def _default_secret_path() -> Path:
    env = os.getenv(SECRET_FILE_ENV_VAR)
    if env:
        return Path(env)
    return Path.home() / ".skillberry" / "wellknown_secret.json"


def _read_secret_file(path: Path) -> Optional[bytes]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.warning("Could not read npx publish secret at %s: %s", path, e)
        return None
    value = data.get("secret") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        logger.warning("npx publish secret at %s has no 'secret' value", path)
        return None
    return value.encode("utf-8")


def _write_secret_file(path: Path, secret: str) -> None:
    """Persist ``secret`` atomically, following the plugins.json precedent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"secret": secret}, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:  # pragma: no cover — best effort on exotic filesystems
        pass
    os.replace(tmp, path)


def load_secret(path: Optional[Path] = None) -> bytes:
    """The durable HMAC secret: from the environment, a file, or freshly made.

    Rotating it is the **global revoke** the derived scheme has, and the only
    one — it is a control to document, not a hazard to warn about (§5.10 #6).

    Precedence:

    1. ``SBS_WELLKNOWN_SECRET`` — authoritative whenever set;
    2. the secret file (``SBS_WELLKNOWN_SECRET_FILE``, else
       ``~/.skillberry/wellknown_secret.json``);
    3. a new random secret, persisted to that file so the next boot reuses it.

    A secret that cannot be persisted is still returned, with a WARNING: the
    feature works for the process lifetime and the operator is told that install
    URLs will change on restart, which is better than refusing to serve.
    """
    env = os.getenv(SECRET_ENV_VAR)
    if env:
        logger.info("npx publish secret: from %s", SECRET_ENV_VAR)
        return env.encode("utf-8")

    target = path if path is not None else _default_secret_path()
    existing = _read_secret_file(target)
    if existing is not None:
        logger.info("npx publish secret: from %s", target)
        return existing

    generated = secrets.token_urlsafe(32)
    try:
        _write_secret_file(target, generated)
        logger.info("npx publish secret: generated and persisted to %s", target)
    except OSError as e:
        logger.warning(
            "npx publish secret: generated but could NOT be persisted to %s (%s); "
            "install URLs will change on restart. Set %s to make them stable.",
            target,
            e,
            SECRET_ENV_VAR,
        )
    return generated.encode("utf-8")


def publish_token(secret: bytes, tenant_id: str, scope: str) -> str:
    """Read capability for ``(tenant_id, scope)``.

    Deliberately **not** ``"{tenant}.{mac}"``: putting the tenant id in the URL
    would leak the username into a shell history and into ``installUrl``
    telemetry for no benefit. Verification recomputes instead (§4.3.3).
    """
    msg = f"{tenant_id}|{scope}".encode("utf-8")
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def candidate_tenants(cfg: Any) -> List[str]:
    """The tenants a publish token may be derived for (§5.10 #4).

    Only tenants with a real ``standalone.users`` entry. That is *simpler* than
    collecting every tenant named in a binding — one comprehension rather than a
    walk over ``bindings[].subjects[]`` with de-duplication — and it excludes
    virtual subjects like ``plugin-user`` for free: they have no password hash
    and cannot log in, so nothing could ever hand them an install URL.
    """
    if cfg is None:
        return []
    seen: List[str] = []
    for user in getattr(cfg, "users", []) or []:
        tenant = getattr(user, "tenant_id", None)
        if tenant and tenant not in seen:
            seen.append(tenant)
    return seen


def resolve_token(
    token: str,
    secret: bytes,
    tenants: Iterable[str],
    scopes: Iterable[str],
) -> Optional[Tuple[str, str]]:
    """Recompute candidate tokens and return the ``(tenant, scope)`` that matches.

    ``O(tenants × scopes)`` HMACs per request — single or double digits for a
    standalone config, and each one is a few microseconds. Comparison is
    constant-time so a prober cannot walk the digest byte by byte.

    Returns ``None`` on any failure, so every caller can 404 uniformly rather
    than distinguishing "no such token" from "not allowed" (§4.3.1).
    """
    if not token:
        return None
    tenant_list = list(tenants)
    scope_list = list(scopes)
    match: Optional[Tuple[str, str]] = None
    for tenant in tenant_list:
        for scope in scope_list:
            if hmac.compare_digest(publish_token(secret, tenant, scope), token):
                # No early return: the loop runs to completion so the work done
                # does not depend on which candidate matched.
                if match is None:
                    match = (tenant, scope)
    return match
