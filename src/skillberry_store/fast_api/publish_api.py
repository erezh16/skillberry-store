# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Per-skill discovery endpoints for ``npx skills add`` (docs/design/npx.md §6.4).

One path prefix, two routes::

    GET /pub/{ref}/.well-known/agent-skills/index.json   discovery index
    GET /pub/{ref}/.well-known/agent-skills/{slug}.zip   the archive

Two, not three. The convention has a second index spelling —
``/.well-known/skills/index.json`` — and the CLI does probe it, but *sequentially*:
it is candidate #2, tried only when #1 fails to parse. Since we always answer #1,
a route for the second spelling is unreachable by ``skills@1.6.0``/``1.7.0`` and
would be public surface with no caller. If a client that speaks only the
``skills/`` spelling ever appears, adding it back is one ``@app.get`` delegating
to the same handler.

Two requests rather than one is imposed by the protocol, not a choice: the index
publishes a digest over bytes the client fetches separately. Schema v0.1.0 would
serve loose files instead, which is *more* requests (one per file) and an
all-or-nothing index failure mode — rejected in §4.7.

``{ref}`` is resolved by access-control mode, which is what keeps the install
command's shape identical across deployments — only what fills ``{ref}`` differs:

==============  ==============================  =====================================
ACL mode        ``{ref}`` is                    Resolution
==============  ==============================  =====================================
``standalone``  a scoped capability token       recompute the HMAC over the candidate
                                                ``(tenant, scope)`` pairs, then re-run
                                                ``authorize(subject, "skills",
                                                "list")`` on **every** request
``disabled``    the scope, spelled out          no auth layer exists, so there is
                (a bare slug is shorthand       nothing for a seed to protect;
                for ``skill:<slug>``)           ``/pub/pdf-forms`` is the whole URL
==============  ==============================  =====================================

A plaintext scope is accepted **only** in ``mode: disabled``. Under
``standalone`` the token is the sole key, so a prober cannot reach a skill by
guessing its name.

There is no root and no unscoped aggregate index in either mode (§5.10 #3), so
an aggregate index cannot be reached by accident and the CLI's own
``WellKnownScopeNotFoundError`` — "not falling back to the root skills index
because that would install every skill the host publishes" — never has to save
us.

Four handler rules, each with a concrete failure mode behind it:

1. **Call the service layer directly, never ``StoreAPI``.** On an allow-listed
   path the PEP returns before ``set_current_subject``, so ``current_subject()``
   is ``None``; ``StoreAPI._admit`` reads it and raises ``PluginIdentityError``,
   which this app maps to a 500.
2. **Never read ``request.state.subject``** — it is only set on the
   authenticated path, so touching it is an ``AttributeError`` on exactly the
   requests this feature serves.
3. **Always JSON, never HTML.** A stray HTML error body is parsed as an index
   and silently yields zero skills, which is how ``npx skills add https://skills.sh``
   reports "no skills found" today (§1.7.1).
4. **404 for every failure** — unknown ref, rotated seed, tenant without
   ``skills:list``, unpublishable skill. Never 403, never anything a prober can
   tell apart (§4.3.1).

Both routes are registered ``include_in_schema=False``, which is what keeps them
out of ``/openapi.json`` — and therefore out of the **generated Python SDK**
(``openapi-generator-cli generate -i .../openapi.json``) and out of the ``sbs``
**CLI**, which restish generates from the same schema. That is deliberate, not an
oversight: these two exist for npx and for nothing else. A generated
``get_publish_index(ref=...)`` would be a client method whose only correct
argument is a capability token, and an `sbs` command for it would invite exactly
the confusion that a publish token is not a session credential. They also carry no
``x-cli-name`` and no ``x-mcp-tool`` marker, so they are absent from the Control
MCP surface for the same reason.

NOTE: no ``@requires`` markers anywhere in this module. These paths are in the
ACL unauthenticated allow-list, so the PEP short-circuits before mapping a route
to ``(resource, verb)`` — ``access_control/decorator.py`` forbids the marker on
allow-listed routes, with ``/health`` as precedent, and ``audit_rbac_coverage``
skips them. The allow-list entry and the absence of a marker are a matched pair:
remove the entry without adding a marker and the routes 403 for everyone, which
is the fail-safe direction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from prometheus_client import Counter

from skillberry_store.tools import publish_refs as refs
from skillberry_store.access_control.config import (
    NPX_PUBLISH_NONE,
    NPX_PUBLISH_SELECTIVE,
)
from skillberry_store.tools.publish import (
    DEFAULT_AGENT,
    PublishedSkill,
    assign_slugs,
    authorized_skills,
    build_index,
    head_skills,
    is_head,
    namespaces_of,
    npx_install_command,
    publishable_entry,
    skill_publishable,
)

logger = logging.getLogger(__name__)

prom_prefix = "sts_publish_"
publish_index_counter = Counter(
    f"{prom_prefix}index_counter",
    "Count of npx discovery-index requests",
    ["update_check"],
)
publish_artifact_counter = Counter(
    f"{prom_prefix}artifact_counter",
    "Count of npx artifact (archive) downloads",
)

#: ``npx skills update`` sets this on its index re-fetch, so an operator can
#: tell update polls from real installs (§1.5, §4.4).
UPDATE_CHECK_HEADER = "X-Skills-Update-Check"


def _not_found() -> HTTPException:
    """The only failure this surface ever reports.

    404 rather than 403 on deny: the CLI needs nothing more than "nothing here",
    and a 403 would confirm to a prober that a guessed token is well-formed
    (§4.3.1).
    """
    return HTTPException(status_code=404, detail="Not Found")


@dataclass(frozen=True)
class ResolvedRef:
    """What a ``{ref}`` path segment turned out to authorize."""

    scope: str  # "skill:<slug>" | "ns:<name>" | "*"
    tenant_id: Optional[str]  # None in mode: disabled
    subject: Optional[Any]  # access_control.pdp.Subject, or None

    @property
    def slug(self) -> Optional[str]:
        return self.scope[len("skill:") :] if self.scope.startswith("skill:") else None


class NpxPublisher:
    """Everything the npx surface needs that is not a pure function.

    Holds the access-control config, the durable HMAC seed and the store's
    public URL, and is reachable from ``app.state.npx`` so the skills API can
    emit the opt-in ``_npx_install`` field without duplicating any of it.

    Constructed even when publishing is off, so ``_npx_install`` has one place
    to ask "is this store publishing?" rather than a second gate of its own.
    """

    def __init__(self, cfg: Any, public_url: Optional[str] = None):
        self.cfg = cfg
        self.public_url = public_url.rstrip("/") if public_url else None
        # Tri-state: "true" | "false" | "selective" (the default). `enabled` is
        # "does this surface exist at all", which is everything but "false";
        # whether a *given* skill is published is `skill_publishable`.
        self.npx_publish = str(
            getattr(cfg, "npx_publish", NPX_PUBLISH_SELECTIVE) or NPX_PUBLISH_SELECTIVE
        )
        self.enabled = self.npx_publish != NPX_PUBLISH_NONE
        self._seed: Optional[bytes] = None

    def publishable(self, skill: Dict[str, Any]) -> bool:
        """Whether ``skill`` is published, under this store's master switch."""
        return skill_publishable(skill, self.npx_publish)

    # -- configuration --------------------------------------------------- #
    @property
    def acl_mode(self) -> str:
        return str(getattr(self.cfg, "mode", "disabled"))

    @property
    def seed(self) -> bytes:
        """The HMAC seed, loaded once on first use.

        Lazy so a store with publishing off never creates a seed file, and so
        importing this module does not touch the filesystem.
        """
        if self._seed is None:
            self._seed = refs.load_seed()
        return self._seed

    def base_url(self, request: Optional[Request]) -> Optional[str]:
        """The absolute base URL to compose an install command from (§5.11).

        Precedence: ``SBS_PUBLIC_URL``, then ``request.base_url``. When neither
        yields an absolute ``http(s)`` URL the caller omits the install command
        rather than emitting one that cannot work — a wrong URL is worse than an
        absent one, because the user only discovers it when npx fails against an
        address that means nothing on their machine.
        """
        if self.public_url:
            return self.public_url
        if request is None:
            return None
        derived = str(request.base_url).rstrip("/")
        if derived.startswith(("http://", "https://")):
            return derived
        return None

    # -- issuing a ref --------------------------------------------------- #
    def ref_for_scope(self, scope: str, tenant_id: Optional[str]) -> Optional[str]:
        """The ``{ref}`` a user pastes for ``scope``.

        In ``standalone`` this is the derived token; in ``disabled`` it is the
        scope itself, with a bare slug as the shorthand.
        """
        if self.acl_mode == "disabled":
            return scope[len("skill:") :] if scope.startswith("skill:") else scope
        if not tenant_id:
            return None
        return refs.derive_ref(self.seed, tenant_id, scope)

    def slug_map(self, service: Any) -> Dict[str, Dict[str, Any]]:
        """``{slug: skill}`` over the store's current HEADs — **every** one.

        Exposed so a *list* handler computes it once rather than once per item:
        the mapping is store-wide by construction (a slug collision is resolved
        across every skill, not within a page).

        Deliberately **unfiltered** by the publish flag. Slug assignment has to be
        stable across store mutations (§5.6), and toggling one skill's flag must
        not renumber anyone else's suffix — which is exactly what would happen if
        collision resolution only saw the published subset. Filtering happens
        after assignment, in :meth:`publishable_slug_map`.
        """
        return assign_slugs(head_skills(service))

    def publishable_slug_map(self, service: Any) -> Dict[str, Dict[str, Any]]:
        """:meth:`slug_map` restricted to the skills this store publishes."""
        return {
            slug: skill
            for slug, skill in self.slug_map(service).items()
            if self.publishable(skill)
        }

    def install_command(
        self,
        request: Optional[Request],
        service: Any,
        skill: Dict[str, Any],
        subject: Optional[Any] = None,
        agent: str = DEFAULT_AGENT,
        slug_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """The per-skill install command for ``skill``, or ``None``.

        ``None`` — rather than a command that would not work — whenever:
        publishing is off; the store does not know its own public URL (§5.11);
        the skill's name has no valid slug; the caller may not list skills
        (§4.3.3, test #19); or ``skill`` is not the HEAD its name resolves to.

        That last one is not an edge case to tidy away: an install URL always
        installs the HEAD (§5.5), so offering one from
        ``GET /skills/{old-uuid}`` would hand the caller a command that installs
        something other than what they are looking at (§5.10 #7).
        """
        if not self.enabled:
            return None
        base = self.base_url(request)
        if not base:
            logger.warning(
                "Omitting npx install command: no SBS_PUBLIC_URL and no usable "
                "request base URL (docs/design/npx.md §5.11)"
            )
            return None
        if not is_head(service, skill):
            return None
        if not self.publishable(skill):
            # Under `selective` this is the normal answer for a skill nobody has
            # opted in: there is no URL to offer, so the UI shows no card.
            return None
        slug = self._slug_for(service, skill, slug_map=slug_map)
        if slug is None:
            return None
        if self.acl_mode != "disabled":
            if not self._may_list(subject):
                return None
            tenant_id = getattr(subject, "tenant_id", None)
            if not tenant_id or tenant_id not in refs.candidate_tenants(self.cfg):
                # A virtual subject (no `standalone.users` entry) cannot be
                # handed an install URL: resolution only ever recomputes over
                # configured tenants, so the token would never verify.
                return None
        else:
            tenant_id = None
        ref = self.ref_for_scope(refs.skill_scope(slug), tenant_id)
        if ref is None:
            return None
        return npx_install_command(base, ref, agent=agent)

    def _slug_for(
        self,
        service: Any,
        skill: Dict[str, Any],
        slug_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """The slug this skill is published under, via the store-wide mapping.

        Resolved through :func:`assign_slugs` rather than ``to_slug`` alone so a
        collision winner and its suffixed runner-up get the slugs they will
        actually be served under.
        """
        mapping = slug_map if slug_map is not None else self.slug_map(service)
        for slug, candidate in mapping.items():
            if candidate.get("uuid") == skill.get("uuid"):
                return slug
        return None

    def _may_list(self, subject: Optional[Any]) -> bool:
        if self.acl_mode == "disabled":
            return True
        if subject is None:
            return False
        from skillberry_store.access_control import pdp

        return pdp.authorize(subject, "skills", "list", self.cfg).allowed

    def _subject_for(self, tenant_id: str) -> Any:
        from skillberry_store.access_control.pdp import Subject

        groups = []
        if self.cfg is not None and hasattr(self.cfg, "groups_for_tenant"):
            groups = list(self.cfg.groups_for_tenant(tenant_id))
        return Subject(tenant_id=tenant_id, groups=groups)

    # -- resolving a ref ------------------------------------------------- #
    def resolve_ref(self, service: Any, ref: str) -> Optional[ResolvedRef]:
        """``{ref}`` → the scope it authorizes, or ``None`` for a uniform 404.

        Under ``standalone`` the candidate scopes are enumerated from the store's
        current HEADs and namespaces, and the token has to match one of them for
        one configured tenant — so a token stops resolving the moment its skill
        is renamed or deleted, and skill A's token can never open skill B's URL
        (§4.3.7).

        Authorization is re-run here on **every** request rather than trusted
        from issue time. That is what makes the common revocation case free: a
        user whose role loses ``skills:list``, or whose account is deleted, stops
        being able to use a URL they already hold (§4.3.3).
        """
        if not self.enabled or not ref:
            return None

        # Only publishable skills are candidates, so an un-opted-in skill's slug
        # (disabled mode) and its derived ref (standalone) both 404 — the same
        # answer a prober gets for a skill that does not exist.
        slugs = self.publishable_slug_map(service)
        namespaces = sorted({n for s in slugs.values() for n in namespaces_of(s)})

        if self.acl_mode == "disabled":
            return self._resolve_plaintext(ref, slugs, namespaces)

        candidate_scopes = (
            [refs.skill_scope(slug) for slug in sorted(slugs)]
            + [refs.namespace_scope(ns) for ns in namespaces]
            + [refs.SCOPE_ALL]
        )
        found = refs.match_ref(
            ref, self.seed, refs.candidate_tenants(self.cfg), candidate_scopes
        )
        if found is None:
            return None
        tenant_id, scope = found
        subject = self._subject_for(tenant_id)
        if not self._may_list(subject):
            logger.info(
                "npx publish denied: tenant %r no longer holds skills:list", tenant_id
            )
            return None
        return ResolvedRef(scope=scope, tenant_id=tenant_id, subject=subject)

    def _resolve_plaintext(
        self, ref: str, slugs: Dict[str, Dict[str, Any]], namespaces: List[str]
    ) -> Optional[ResolvedRef]:
        """``mode: disabled`` — the ref names its own scope, no seed involved."""
        if ref == refs.SCOPE_ALL:
            scope = refs.SCOPE_ALL
        elif ref.startswith("ns:"):
            scope = ref if ref[len("ns:") :] in namespaces else ""
        elif ref.startswith("skill:"):
            scope = ref if ref[len("skill:") :] in slugs else ""
        elif ref in slugs:
            scope = refs.skill_scope(ref)
        else:
            scope = ""
        if not scope:
            return None
        return ResolvedRef(scope=scope, tenant_id=None, subject=None)

    # -- building the published set -------------------------------------- #
    def entries(self, service: Any, resolved: ResolvedRef) -> List[PublishedSkill]:
        """The skills ``resolved`` publishes, as built artifacts.

        A single-entry index builds one archive, which is the whole reason
        per-skill is the default (§4.3.8): a multi-entry index has to build and
        hash every archive before it can answer, because the digest is part of
        the index.
        """
        slugs = self.publishable_slug_map(service)
        if resolved.slug is not None:
            skill = slugs.get(resolved.slug)
            if skill is None:
                return []
            entry = publishable_entry(service, str(skill["uuid"]), resolved.slug)
            return [entry] if entry is not None else []

        # Multi-entry (`ns:` / `*`). Routed through `authorized_skills` so there
        # is exactly one place a future per-namespace binding model would make
        # the filter real, rather than an inlined "everything, because
        # visibility is binary today" (§4.3.9).
        allowed = {
            s.get("uuid")
            for s in authorized_skills(
                service, resolved.subject, self.cfg, scope=resolved.scope
            )
        }
        entries: List[PublishedSkill] = []
        for slug in sorted(slugs):
            skill = slugs[slug]
            if skill.get("uuid") not in allowed:
                continue
            entry = publishable_entry(service, str(skill["uuid"]), slug)
            if entry is not None:
                entries.append(entry)
        return entries

    def publishable_count(self, service: Any) -> int:
        """How many skills this store would publish — for the boot log line.

        Counts what the master switch and the per-skill flags actually allow, so
        under ``selective`` a store where nobody has opted a skill in logs ``0``
        and the operator can see that immediately rather than after a failed
        install.
        """
        try:
            return len(self.publishable_slug_map(service))
        except Exception as e:  # noqa: BLE001 — a log line must not break boot
            logger.warning("Could not count publishable skills: %s", e)
            return 0


# --------------------------------------------------------------------------- #
# The opt-in `_npx_install` field on GET /skills/ and GET /skills/{id} (§4.3.5)
# --------------------------------------------------------------------------- #
#: Keys the install command is *decided* from, which the caller need not have
#: asked for: ``uuid`` and ``name`` decide whether the skill is the HEAD and which
#: slug it is published under, and ``npx_publish`` decides whether it is published
#: at all under a ``selective`` master switch.
#:
#: Every one of these has to be widened into the service's field allowlist and
#: stripped again afterwards. Miss one and the decision silently reads ``None``
#: from a projected-away key: omitting ``npx_publish`` here made
#: ``?fields=name,_npx_install`` return no command for a skill that *was* opted
#: in, because the flag had been projected out before the gate looked at it.
_NPX_IDENTITY_FIELDS = {"uuid", "name", "npx_publish"}

#: Shape of an agent name the CLI's ``-a`` flag accepts. A regex rather than an
#: allow-list on purpose: the CLI supports ~75 agents and adds more, so a list
#: maintained here would drift, and the CLI validates the name itself. What this
#: has to guarantee is only that nothing can be smuggled into the command string
#: we hand the user to paste into a shell.
_AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,39}$")


def normalise_agent(agent: Optional[str]) -> str:
    """The agent to pin with ``-a``, falling back to the default.

    ``-a`` is never omitted: ``-y`` with no detected agent installs the skill
    into every supported agent's directory — around 75 of them in the user's
    tree (§4.3.1).
    """
    if agent and _AGENT_RE.match(agent):
        return agent
    if agent:
        logger.info(
            "Ignoring unusable npx agent name %r; using %s", agent, DEFAULT_AGENT
        )
    return DEFAULT_AGENT


def npx_install_requested(fields: Optional[str], agent: Optional[str] = None) -> bool:
    """Whether this request asks for ``_npx_install``.

    Two ways to ask, because one of them was a trap:

    * naming ``_npx_install`` in a CSV ``fields`` allowlist — it carries no preset
      tag, so a preset never selects it (§4.3.5);
    * passing ``npx_agent`` at all. That parameter pins the ``-a`` flag in the
      install command and has **no other effect**, so a caller who passes it and
      gets no command back has been silently ignored. ``sbs get-skill <name>
      --npx-agent claude-code`` returning a skill with no install information was
      reported as a bug, and it was one: asking which agent to target is asking
      for the command.

    Note what is deliberately *not* here: ``fields=full``. That preset is used
    internally — gathering export inputs, populating skills for the vMCP bundle —
    and none of those paths should start returning a capability URL (§4.3.5).
    """
    from skillberry_store.services.field_selection import (
        parse_fields_spec,
        should_run_mechanism,
    )

    if agent is not None:
        return True
    return should_run_mechanism(parse_fields_spec(fields, "skill"), "_npx_install")


def expand_npx_fields(
    fields: Optional[str], agent: Optional[str] = None
) -> "tuple[Optional[str], set]":
    """Widen ``fields`` with the identity keys ``_npx_install`` needs.

    Returns ``(fields_to_pass_to_the_service, keys_to_strip_afterwards)``.

    Composing an install command needs the skill's ``uuid`` and ``name``, but a
    caller is entitled to ask for ``fields=_npx_install`` alone and get exactly
    that back. So the allowlist is widened on the way in and narrowed again on
    the way out, which keeps the response shape the caller's choice and costs no
    extra store read.

    Passing ``npx_agent`` with a preset works the same way: the preset is expanded
    to an explicit list so the identity fields are present, and anything the
    caller did not ask for is stripped again on the way out.
    """
    from skillberry_store.services.field_selection import parse_fields_spec

    if not npx_install_requested(fields, agent):
        return fields, set()
    allow = parse_fields_spec(fields, "skill")
    needed = _NPX_IDENTITY_FIELDS - allow
    if not needed:
        return fields, set()
    return ",".join(sorted(allow | needed)), needed


def attach_npx_install(
    request: Request,
    service: Any,
    fields: Optional[str],
    items: List[Dict[str, Any]],
    strip: Optional[set] = None,
    agent: Optional[str] = None,
) -> None:
    """Set ``_npx_install`` on each of ``items``, in place; a no-op if unasked.

    Silently omits the field for any skill that has no usable command — a
    superseded version, a name with no valid slug, a caller without
    ``skills:list``, a store with publishing off, or a store that does not know
    its own public URL. Omission rather than an error is deliberate: the caller
    asked for a convenience, and there is nothing they could do about any of
    those conditions.
    """
    if not npx_install_requested(fields, agent):
        return
    publisher: Optional[NpxPublisher] = getattr(request.app.state, "npx", None)
    if publisher is not None and publisher.enabled and items:
        subject = getattr(request.state, "subject", None)
        slug_map = publisher.slug_map(service)
        pinned = normalise_agent(agent)
        for item in items:
            command = publisher.install_command(
                request, service, item, subject=subject, agent=pinned, slug_map=slug_map
            )
            if command:
                item["_npx_install"] = command
    for item in items:
        for key in strip or set():
            item.pop(key, None)


def register_publish_api(
    app: FastAPI,
    publisher: Optional[NpxPublisher] = None,
    service: Optional[Any] = None,
) -> None:
    """Register the npx discovery routes, if this store publishes for npx.

    Route registration is **gated on the flag** rather than the handlers
    refusing: when ``npx_publish`` is off the surface genuinely does not exist,
    so ``/pub/<anything>`` is a plain 404 and no route appears in the app's route
    table (§5.12).

    Emits one boot log line either way, so "is this store exposed?" is
    answerable from the log.
    """
    if publisher is None:
        publisher = getattr(app.state, "npx", None)
    if publisher is None:
        raise RuntimeError("register_publish_api requires an NpxPublisher")
    if service is None:
        from skillberry_store.services.registry import get_service

        service = get_service("skill")

    if not publisher.enabled:
        logger.info(
            "npx publishing is OFF (npx_publish=%s) — /pub/* is not registered",
            publisher.npx_publish,
        )
        return

    logger.info(
        "npx publishing is ON (npx_publish=%s, acl mode=%s, %d skill(s) "
        "publishable now, public_url=%s) — serving SKILL.md content at "
        "GET /pub/{ref}/.well-known/agent-skills/*",
        publisher.npx_publish,
        publisher.acl_mode,
        publisher.publishable_count(service),
        publisher.public_url or "(derived per request)",
    )
    if publisher.npx_publish == NPX_PUBLISH_SELECTIVE:
        logger.info(
            "npx_publish=selective — each skill's own `npx_publish` flag decides; "
            "a skill that has not set it is not published"
        )

    # The two routes are matched by a distinct final segment — `index.json` does
    # not end in `.zip` — so neither can shadow the other whatever the
    # registration order (§5.9).
    #
    # GET only. `@app.get` does not imply HEAD, so a HEAD here answers 405 — the
    # CLI never issues one (§5.8 #4), and adding it would require a matching
    # `HEAD /pub/*` allow-list entry since the audit requires every method on a
    # route to be allow-listed.
    @app.get(
        "/pub/{ref}/.well-known/agent-skills/index.json",
        # Out of /openapi.json, hence out of the generated SDK and the `sbs` CLI
        # — see the module docstring. Do not add x-cli-name or x-mcp-tool.
        include_in_schema=False,
    )
    def publish_index(request: Request, ref: str) -> Dict[str, Any]:
        """The discovery index for ``ref``'s scope."""
        publish_index_counter.labels(
            update_check=str(request.headers.get(UPDATE_CHECK_HEADER) == "1").lower()
        ).inc()
        resolved = publisher.resolve_ref(service, ref)
        if resolved is None:
            raise _not_found()
        entries = publisher.entries(service, resolved)
        if not entries and resolved.slug is not None:
            # An empty index is a legitimate answer for a namespace that has been
            # emptied — `npx skills update` reads it as "deleted upstream" and
            # offers to remove the local copy. For a per-skill ref it instead
            # means the skill is unpublishable, which is a 404.
            raise _not_found()
        return build_index(entries)

    @app.get(
        "/pub/{ref}/.well-known/agent-skills/{slug}.zip",
        # Out of /openapi.json, hence out of the generated SDK and the `sbs` CLI
        # — see the module docstring. Do not add x-cli-name or x-mcp-tool.
        include_in_schema=False,
    )
    def publish_artifact(
        ref: str,
        slug: str,
        digest: Optional[str] = Query(
            None,
            description=(
                "Serve exactly the bytes with this digest, from cache, or 404. "
                "Closes the index/artifact race (docs/design/npx.md §5.14)."
            ),
        ),
    ) -> Response:
        """One skill's archive, resolved under the same ``{ref}`` as its index.

        The index publishes a **relative** ``url``, which the CLI resolves
        against the URL it fetched the index from — so this archive is protected
        by the same prefix as the index, and the token never appears in the
        index body (§5.14).
        """
        resolved = publisher.resolve_ref(service, ref)
        if resolved is None:
            raise _not_found()
        # A ref may only reach a slug inside its own scope: a per-skill token
        # cannot fetch another skill's archive (§4.3.7, test #21).
        if resolved.slug is not None and resolved.slug != slug:
            raise _not_found()

        if digest:
            from skillberry_store.tools.publish import get_cache

            slugs = publisher.publishable_slug_map(service)
            skill = slugs.get(slug)
            entry = (
                get_cache().by_digest(str(skill["uuid"]), slug, digest)
                if skill is not None
                else None
            )
            if entry is None:
                # A miss, not a fallback to current bytes: the caller asked for
                # specific bytes, and quietly substituting different ones is the
                # very failure the selector exists to prevent.
                raise _not_found()
        else:
            entry = next(
                (e for e in publisher.entries(service, resolved) if e.slug == slug),
                None,
            )
            if entry is None:
                raise _not_found()

        publish_artifact_counter.inc()
        return Response(
            content=entry.payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{slug}.zip"',
                # The digest of exactly these bytes, so a proxy or an operator
                # can verify what was served without unzipping it.
                "X-Skill-Digest": entry.digest,
            },
        )
