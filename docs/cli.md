# Skillberry Store CLI Documentation

The Skillberry Store SDK includes an auto-generated command-line interface (CLI) that provides convenient access to all API operations from your terminal.

## Overview

The CLI is built as a wrapper around [restish](https://rest.sh/), a powerful REST API client that automatically generates commands from OpenAPI specifications. This means:

- **Auto-generated commands** for all API endpoints
- **Type-safe parameters** based on the OpenAPI schema
- **Automatic documentation** from API descriptions
- **Multiple output formats** (JSON, YAML, table)

## Installation

The CLI is included with the Python SDK:

```bash
pip install skillberry-store-sdk
```

### Prerequisites

The CLI requires `restish` to be installed. If not present, the CLI will provide installation instructions:

**Option 1: Using Go**
```bash
go install github.com/rest-sh/restish@latest
```

**Option 2: Download pre-built binaries**
- Visit [restish releases](https://github.com/rest-sh/restish/releases)
- Download the appropriate binary for your platform
- Add it to your PATH

## Basic Usage

The CLI command is `sbs` (Skillberry Store):

```bash
sbs --help                    # Show all available commands
sbs <command> [args] [flags]  # General command structure
```

## Configuration

### Default Connection

By default, the CLI connects to `http://0.0.0.0:8000`. The configuration is automatically created in `~/.config/restish/apis.json` on first use.

### Connecting to Different Servers

To connect to a different server:

```bash
sbs connect http://production-server:8000
sbs connect https://staging.example.com
```

The CLI will remember this connection for future commands.

## Command Reference

### Skills Management

```bash
# List all skills
sbs list-skills

# Get a specific skill
sbs get-skill <skill-name>

# Create a new skill
sbs create-skill --name my-skill --description "My skill"

# Update a skill
sbs update-skill <skill-name> --description "Updated description"

# Delete a skill
sbs delete-skill <skill-name>

# Search skills semantically
sbs search-skills --search-term "data processing"

# Anthropic skill import/export
sbs detect-anthropic-skills
sbs import-anthropic-skill
sbs export-anthropic-skill <skill-name>
```

### Tools Management

```bash
# List all tools
sbs list-tools

# Get a specific tool
sbs get-tool <tool-name>

# Get the source module of a tool
sbs get-tool-module <tool-name>

# Search tools semantically
sbs search-tools --search-term "calculator functions"

# Execute a tool
sbs execute-tool <tool-name> --body='{"params":{"x":5,"y":3}}'

# Add a tool from a Python file
sbs add-tool

# Update or delete a tool
sbs update-tool <tool-name>
sbs delete-tool <tool-name>
```

### Snippets Management

```bash
# List all snippets
sbs list-snippets

# Get a specific snippet
sbs get-snippet <snippet-name>

# Create a snippet
sbs create-snippet --name helper --description "Helper utilities"

# Update or delete a snippet
sbs update-snippet <snippet-name>
sbs delete-snippet <snippet-name>

# Search snippets semantically
sbs search-snippets --search-term "string utilities"
```

### VMCP Servers

```bash
# List virtual MCP servers
sbs list-vmcp-servers

# Get a specific VMCP server
sbs get-vmcp-server <server-name>

# Create a VMCP server
sbs create-vmcp-server --name my-server --skill-uuid <uuid>

# Start, update, or delete a VMCP server
sbs start-vmcp-server <server-name>
sbs update-vmcp-server <server-name>
sbs delete-vmcp-server <server-name>

# Search VMCP servers
sbs search-vmcp-servers --search-term "math tools"
```

### vNFS Servers

```bash
# List virtual NFS servers
sbs list-vnfs-servers

# Get a specific vNFS server
sbs get-vnfs-server <server-name>

# Create a vNFS server
sbs create-vnfs-server --name my-server --skill-uuid <uuid>

# Start, update, or delete a vNFS server
sbs start-vnfs-server <server-name>
sbs update-vnfs-server <server-name>
sbs delete-vnfs-server <server-name>

# Search vNFS servers
sbs search-vnfs-servers --search-term "data files"
```

### Admin

```bash
# Health checks
sbs health
sbs health-ready

# Prometheus metrics
sbs metrics

# Delete all data (irreversible)
sbs purge-all
```

## Advanced Features

### Output Formats

Restish supports multiple output formats:

```bash
# JSON output (default)
sbs list-tools

# YAML output
sbs list-tools -o yaml

# Table output
sbs list-tools -o table

# Raw output
sbs list-tools -o raw
```

### Filtering and Pagination

```bash
# Filter results
sbs list-tools --filter='state=active'

# Limit results
sbs list-tools --limit=10

# Pagination
sbs list-tools --offset=20 --limit=10
```

### Request Body from File

For complex requests, use a file:

```bash
# Create tool from JSON file
sbs create-tool --body=@tool-definition.json

# Update skill from YAML file
sbs update-skill <skill-name> --body=@skill-update.yaml
```

### Verbose Mode

See detailed request/response information:

```bash
sbs list-tools -v
```

## Architecture

The CLI works through the following flow:

1. **Auto-configuration**: On first run, the CLI:
   - Creates `~/.config/restish/apis.json`
   - Registers the API with the OpenAPI spec URL
   - Syncs the spec to generate commands

2. **Command delegation**: All commands are passed to `restish`:
   ```
   sbs list-tools → restish sbs list-tools
   ```

3. **Output filtering**: The CLI filters restish output to:
   - Remove generic "Global Flags" section
   - Add custom help text
   - Show current connection URL

## Troubleshooting

### CLI not found after installation

Ensure the Python scripts directory is in your PATH:

```bash
# Linux/macOS
export PATH="$HOME/.local/bin:$PATH"

# Windows
# Add %APPDATA%\Python\Scripts to your PATH
```

### Restish not installed

Follow the installation instructions provided by the CLI or visit [restish documentation](https://rest.sh/).

### Connection refused

Ensure the Skillberry Store service is running:

```bash
# Check if service is running
curl http://localhost:8000/docs

# Start the service
make run
```

### API spec sync failed

Manually sync the API spec by clearing the cache and re-running any command:

```bash
rm ~/.cache/restish/sbs.cbor
sbs --help
```

## Examples

### Complete Workflow Example

```bash
# 1. Connect to your server
sbs connect http://localhost:8000

# 2. List existing tools
sbs list-tools

# 3. Add a new tool
sbs create-tool --name calculator --description "Basic calculator"

# 4. Execute the tool
sbs execute-tool calculator --body='{"params":{"x":5,"y":3}}'

# 5. Create a skill using the tool
sbs create-skill --name math-skill --description "Math utilities"

# 6. List skills to verify
sbs list-skills
```

### Batch Operations

```bash
# Export all tools
sbs list-tools -o json > tools-backup.json

# Import tools from backup
cat tools-backup.json | jq -c '.[]' | while read tool; do
  sbs create-tool --body="$tool"
done
```

## Install skills into your agent with npx

A skill in the store can be installed straight into Claude Code, Cursor, Codex or
any of ~70 other agents with one command and no prior setup — no `npm install`,
no `skills` CLI on your PATH, no git credentials, no login from the terminal:

```bash
npx skills add https://store.example.com/pub/pdf-forms -y -a claude-code
```

`npx` fetches the [`skills`](https://www.npmjs.com/package/skills) CLI on first
use and caches it. The store serves the skill's bytes directly over two
read-only `GET` endpoints, using the open
`/.well-known/agent-skills/index.json` discovery convention. Those two endpoints
exist for npx only: they are excluded from `/openapi.json`, so neither the
generated SDK nor `sbs` has a method or command for them.

**You do not have to compose that command.** Every surface hands you the whole
thing, flags included:

```bash
# one skill
sbs get-skill pdf-forms --fields name,_npx_install

# every skill you can see, each with its own command
sbs list-skills --fields name,_npx_install
```

In the UI, each skill's detail page has an **Install with npx** card with an
agent picker and a copy button.

### Installing several skills

There is deliberately one skill per install URL, so bulk installs are scripted
rather than served by a single aggregate URL:

```bash
sbs list-skills --fields _npx_install -o json \
  | jq -r '.[]._npx_install' \
  | while read -r cmd; do eval "$cmd"; done
```

Each `npx` run re-does discovery, but the npm package is cached after the first,
so this is seconds per skill.

### Keeping up to date

```bash
npx skills update
```

Re-fetches the index and re-downloads only the skills whose content changed. A
store-side edit reaches you on your next `update`; a skill deleted from the store
is offered for removal.

### What the flags are for

Both matter, and both are emitted for you — they are not decoration:

| Flag | Why |
| --- | --- |
| `-a <agent>` | `-y` with no detected agent installs the skill into **every** supported agent's directory — around 75 of them in your project tree. |
| `-y` | Skips the scope, symlink and confirmation prompts. |

### Telemetry, and how to opt out

On a **successful** install (not on discovery, and not on `--list`) the CLI sends
a request to `add-skill.vercel.sh` carrying your hostname, the skill name, the
artifact URL, and `installUrl` — the URL you typed, which on a secured store
contains an access token. **File contents are never sent.** The CLI's own
privacy-suppression path only understands GitHub repository visibility, so it has
no concept of a private well-known host and reports an intranet store like a
public one.

Opt out once, for every run:

```bash
# bash / zsh — in ~/.bashrc, ~/.zshrc, or your CI environment
export DO_NOT_TRACK=1
```

```powershell
# PowerShell — in $PROFILE
$env:DO_NOT_TRACK = '1'
```

`DISABLE_TELEMETRY=1` has the same effect; `DO_NOT_TRACK` is the
[cross-vendor convention](https://consoledonottrack.com/), so one export also
covers the other CLIs that honour it.

The install command the store emits deliberately carries **no** environment-variable
prefix, for three reasons:

- a prefix protects exactly one invocation — every later `npx skills update` you
  type yourself would be unaffected, so the opt-out has to live in your
  environment to mean anything;
- `VAR=1 command` is POSIX shell syntax, so a prefixed command would simply fail
  in PowerShell and `cmd.exe`;
- suppressing by default would quietly remove every SBS install from the
  ecosystem's install counts, which is not a decision a store should make on its
  users' behalf without saying so.

The trade-off is explicit: the default reports the install, and on a secured store
that report includes the token. If that is unacceptable for your deployment, set
`DO_NOT_TRACK=1` in the environment your users' shells inherit — and note that
the UI already says this next to the copy button, where someone about to paste a
credential-bearing URL will see it.

### Operator notes

npx publishing is **off by default** and is turned on with `npx_publish: true` in
`access_control_config.yaml`. It is declared there rather than in an environment
variable because it *is* an access-control decision: it is the one setting that
makes skill **content**, not just metadata, readable without a session.

| Concern | What to know |
| --- | --- |
| **What gets published** | Every skill visible to a holder of `skills:list`, one per install URL. There is no lifecycle-state or tag filter: a `state: new` draft is already visible to every such user, so hiding it from npx would misreport what the store contains. To publish a curated subset, use a namespace — which users can see and filter by — rather than an invisible server-side filter. |
| **`SBS_PUBLIC_URL`** | Set it. The install command is absolute, and behind an ingress or load balancer the server cannot derive its own externally-visible URL; without it, the command is omitted rather than guessed. Useful beyond npx — it is the value any copy-paste snippet needs. |
| **Access tokens in URLs** | With access control on, the path segment is a read-only capability token scoped to **one skill**, derived from a durable seed (`SBS_PUBLISH_SEED`, which must be treated as confidential — anyone holding it can derive a URL for any skill without authenticating). It is re-authorized on every request, so it stops working the moment its tenant loses `skills:list` or its account is removed. It is not accepted as an `Authorization: Bearer` value anywhere. Note that it travels to `add-skill.vercel.sh` in `installUrl` unless `DO_NOT_TRACK=1` is set — see above. |
| **The token is durable** | It keeps returning *future* edits to that skill, not only the version installed, until the secret is rotated. Where the skill's content is committed next to the lockfile that is immaterial; it matters if a published skill later gains sensitive content. |
| **Revoking** | Losing `skills:list` revokes automatically, per request. To revoke everything at once, rotate `SBS_PUBLISH_SEED` — that is the intended global revoke, and everyone then re-copies their command. |
| **Committing `skills-lock.json`** | Safe. A project install writes the lockfile *and* the skill's own files in the same commit, so a per-skill token in it grants read access to content that is already in the repository beside it. |
| **Alternatives to a URL-borne token** | Running with access control disabled behind a network boundary (the URL is then just the skill's slug, with no secret), terminating auth at a gateway in front of the store, or leaving npx publishing off entirely and using the UI, the `sbs` CLI or vNFS. All three are supported configurations, not failure modes. |
| **Hiding one skill** | Tag it `npx-internal`. Its own install URL keeps working — it has to, or the command on its page would break — but its emitted frontmatter carries `metadata.internal: true`, which the CLI honours by leaving it out of a multi-skill install list unless `INSTALL_INTERNAL_SKILLS=1`. |
| **Namespace packs** | A namespace-scoped install URL publishes one namespace, which is the analogue of a skills.sh "pack". Set `SBS_PUBLISH_NAMESPACES` to a comma-separated allowlist to restrict which namespaces may be named that way; per-skill URLs are unaffected. |
| **Renaming a skill** | Changes its slug, so it leaves the index under the old name and reappears under the new one; an already-installed copy is orphaned. Expected, not a defect. |
| **Version chains** | An install URL always installs the HEAD. On a chain with no unambiguous HEAD, which object is published follows the same resolution as `GET /skills/{name}` — debug the chain, not the index. |

See [docs/design/npx.md](design/npx.md) for the protocol findings this is built
on, including what the CLI validates and what it silently drops.

## Integration with Scripts

The CLI can be easily integrated into shell scripts:

```bash
#!/bin/bash

# Check if tool exists
if sbs get-tool my-tool 2>/dev/null; then
  echo "Tool exists"
else
  echo "Creating tool..."
  sbs create-tool --body=@tool.json
fi

# Get tool output as JSON
RESULT=$(sbs execute-tool my-tool --body='{"params":{}}' -o json)
echo "Result: $RESULT"
```

## Related Documentation

- [Skillberry Store API Documentation](http://localhost:8000/docs)
- [Restish Documentation](https://rest.sh/)
- [Python SDK Documentation](https://github.com/skillberry-ai/skillberry-store-sdk)
- [Configuration Guide](config-env-vars.md)

## Support

For issues or questions:
- Check the [main README](../README.md)
- Review [API documentation](http://localhost:8000/docs)
- Contact the development team
