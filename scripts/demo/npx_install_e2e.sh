#!/usr/bin/env bash
# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
#
# Manual end-to-end check of `npx skills add` against a running store.
# docs/design/npx.md §6.6.
#
# Deliberately NOT part of `make test`: it needs network access (npx fetches the
# `skills` package from the public npm registry) and Node >= 22.20.0. The
# in-process equivalents — index shape, digest agreement, archive layout, ACL
# behaviour — are covered by
# src/skillberry_store/tests/fast_api/test_wellknown_api.py, which asserts
# against the CLI's own validators. What this script adds is the one thing a
# TestClient cannot: that the real CLI, unmodified, installs the skill.
#
# Usage:
#   # in one terminal, with npx publishing on
#   export SBS_PUBLIC_URL=http://localhost:8000
#   make run
#
#   # in another
#   scripts/demo/npx_install_e2e.sh [skill-name]
#
# Environment:
#   SBS_URL     store base URL                (default http://localhost:8000)
#   SBS_TOKEN   session bearer                (required when ACL is on)
#   AGENT       agent to install into          (default claude-code)
#   WORKDIR     where to install              (default a fresh mktemp -d)

set -euo pipefail

SBS_URL="${SBS_URL:-http://localhost:8000}"
AGENT="${AGENT:-claude-code}"
SKILL="${1:-}"

# Do not report this host, this URL or these skill names to Vercel (§1.8). The
# install URL can contain an access token, so this is not optional here.
#
# Exported for the whole script rather than prefixed onto each `npx` call: that
# is also the form the docs recommend to users, because it covers every later
# `npx skills update` and not just one invocation. DO_NOT_TRACK is the
# cross-vendor convention; the CLI honours it and DISABLE_TELEMETRY alike.
export DO_NOT_TRACK=1

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*" >&2; exit 1; }

command -v npx >/dev/null || fail "npx not found — install Node >= 22.20.0"
node_major="$(node -p 'process.versions.node.split(".")[0]')"
[ "$node_major" -ge 22 ] || fail "the skills CLI needs Node >= 22.20.0 (found $(node -v))"

auth=()
if [ -n "${SBS_TOKEN:-}" ]; then
    auth=(-H "Authorization: Bearer ${SBS_TOKEN}")
fi

say "Store health"
curl -fsS "${SBS_URL}/health" || fail "store not reachable at ${SBS_URL}"
echo

say "Is the store locked down? (informational)"
skills_status="$(curl -s -o /dev/null -w '%{http_code}' "${SBS_URL}/skills/?fields=narrow")"
echo "GET /skills/ without a token -> ${skills_status}"
if [ "$skills_status" = "401" ] && [ -z "${SBS_TOKEN:-}" ]; then
    fail "access control is on; set SBS_TOKEN to a session bearer (sbs login, or POST /auth/login)"
fi

if [ -z "$SKILL" ]; then
    say "Picking the first skill in the store"
    SKILL="$(curl -fsS "${auth[@]}" "${SBS_URL}/skills/?fields=name" \
        | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$SKILL" ] || fail "no skills in the store — create one first"
fi
echo "skill: ${SKILL}"

say "Asking the store for the install command"
# The store composes the whole command, flags included. Never hand-assemble it:
# `-a` must be explicit (`-y` with no detected agent installs into ~75 agent
# directories) and there is one definition of this string, server-side.
response="$(curl -fsS "${auth[@]}" --get \
    --data-urlencode 'fields=_npx_install' \
    --data-urlencode "npx_agent=${AGENT}" \
    "${SBS_URL}/skills/${SKILL}")"
command_line="$(printf '%s' "$response" \
    | sed -n 's/.*"_npx_install":"\([^"]*\)".*/\1/p')"
if [ -z "$command_line" ]; then
    fail "no _npx_install in the response. Is npx_publish: true in access_control_config.yaml, and is SBS_PUBLIC_URL set? Response: ${response}"
fi
echo "$command_line"

install_url="$(printf '%s' "$command_line" | sed -n 's/.* add \([^ ]*\).*/\1/p')"

say "Discovery, with no credentials at all"
index="$(curl -fsS "${install_url}/.well-known/agent-skills/index.json")"
echo "$index"
printf '%s' "$index" | grep -q '"\$schema":"https://schemas.agentskills.io/discovery/0.2.0/schema.json"' \
    || fail "index is missing the exact v0.2.0 \$schema literal (the CLI compares it as a constant)"
slug="$(printf '%s' "$index" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -1)"
published_digest="$(printf '%s' "$index" | sed -n 's/.*"digest":"sha256:\([a-f0-9]*\)".*/\1/p' | head -1)"
[ -n "$slug" ] || fail "index published no skill"

say "The alias the CLI also probes"
curl -fsS -o /dev/null "${install_url}/.well-known/skills/index.json" \
    || fail "/.well-known/skills/index.json does not answer"
echo "ok"

say "Artifact bytes hash to the published digest"
# A mismatch is not an error message — the skill just vanishes from the install
# list (§1.4), so this is the single most valuable check in the script.
artifact="$(mktemp)"
curl -fsS -o "$artifact" "${install_url}/.well-known/agent-skills/${slug}.zip"
actual="$(sha256sum "$artifact" | cut -d' ' -f1)"
echo "published ${published_digest}"
echo "actual    ${actual}"
[ "$actual" = "$published_digest" ] || fail "digest mismatch — the CLI would silently drop this skill"

say "The archive has SKILL.md at its root"
unzip -l "$artifact" || fail "not a readable zip"
unzip -l "$artifact" | grep -qE ' SKILL\.md$' \
    || fail "no root-level SKILL.md — the CLI rejects the archive"
rm -f "$artifact"

WORKDIR="${WORKDIR:-$(mktemp -d)}"
say "Installing for real into ${WORKDIR}"
pushd "$WORKDIR" >/dev/null
eval "$command_line"

say "What landed on disk"
find . -maxdepth 4 -name 'SKILL.md' -o -maxdepth 2 -name 'skills-lock.json' | sort
[ -f skills-lock.json ] || fail "no skills-lock.json written"

say "Provenance recorded in the lockfile"
cat skills-lock.json
grep -q '"sourceType": *"well-known"' skills-lock.json \
    || fail 'lockfile does not record sourceType: "well-known"'
grep -q 'wellKnownDigest' skills-lock.json \
    || fail "lockfile does not record wellKnownDigest"

say "update is a no-op while the store has not changed"
npx skills update

cat <<EOF

$(printf '\033[32mPASSED\033[0m')

Installed into: ${WORKDIR}

Remaining manual step — the part only a human can judge:

  1. Edit '${SKILL}' in the store (UI, or PUT /skills/${SKILL}).
  2. cd ${WORKDIR} && npx skills update
     It should re-download exactly that skill and nothing else.
  3. Open the installed SKILL.md and confirm the frontmatter 'name' matches its
     own directory name ('${slug}'), and that the description reads as written —
     no truncation at a ':' and no inserted line breaks.

EOF
popd >/dev/null
