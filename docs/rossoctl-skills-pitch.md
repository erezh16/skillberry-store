# Skills that actually do things: closing the agent ↔ skill gap in rossoctl

rossoctl already ships skills to agents. A skill registry (skillberry-store, or a local ConfigMap) is synced into the namespace. Linking a skill to an agent adds a `fetch-skill-*` init container that unpacks the skill to `/app/skills/<name>`, and `SKILL_FOLDERS` tells the agent where it is.

That is half the story. The skill arrives in the pod, but in most cases nothing uses it beyond reading it. This note lays out what we found, then a ladder of fixes, cheapest first.

## Where we are today

Checked against `rossoctl/rossoctl@1cac9f3f` and `rossoctl/examples@f1fc5df2`.

**The agent contract has no skills in it.** "Bring your own agent" requires A2A over HTTP, plus `LLM_*`, `MCP_URL` and `MCP_URLS`. `SKILL_FOLDERS` is set, but no agent is obliged to read it.

**What the example agents do with a linked skill:**

| Agent | Reads the skill | Runs its scripts | Tools |
|---|---|---|---|
| `generic_agent` | Pastes SKILL.md and every `*.py` into the system prompt (100 KB cap) | No: it binds MCP tools only | many, via `MCP_URLS` |
| `weather_service`, `file_organizer`, `image_service`, `reservation_service` (LangGraph, same layout) | No | No | one `MCP_URL` |
| AG2, CrewAI, Marvin, plain-OpenAI agents | No | No (the AG2 agents set `code_execution_config=False`) | one `MCP_URL`, or built-in |
| `claude_agent` (not a console preset) | No: `SKILL_FOLDERS` is ignored, and nothing links `/app/skills` into `~/.claude/skills` | **Could:** Claude Code runs with permissions skipped, but is never pointed at the skill | Claude Code's own |
| sandbox `agent_server` | Puts `.claude/skills/*` into the prompt | No: there is no tool loop | none |

**Pasting a script isn't running it.** The model can read `scripts/x.py` but has no tool to execute it. The file is already on disk, so "write it out and run it" isn't the missing step; *execution* is.

**This isn't a security stance.** The docs treat code-running agents as supported:
- [Sandboxes](https://github.com/rossoctl/rossoctl/blob/main/docs/concepts/experiments/sandboxes.md): "use a sandbox for an agent that runs code that you do not control".
- The internal skill-execution e2e expects agents to *execute* skills.
- The security model puts enforcement in the AuthBridge sidecar, which "does not depend on the behaviour or the quality of the code of the agent".

So the gap is in the agents we ship, not a platform rule.

**Tools and skills are wired separately.** A skill that needs `weather-tool` and `reservation-tool` can't say so in a way rossoctl acts on. The user must set `MCP_URLS` (or point `MCP_URL` at the MCP Gateway) by hand, and only `generic_agent` takes more than one URL.

**Net effect.** Today a skill is a prompt fragment. It can't carry working tools, and the tools it relies on are wired by hand on the agent.

## What "good" looks like

> Link a skill to an agent. The agent can follow its instructions **and** use the tools it needs: the skill's own scripts, and the rossoctl tools it depends on. It does this in whichever namespace it runs, under the same AuthBridge policy as everything else.

Skills composed in skillberry-store can already package the second part as plain local scripts. One script per rossoctl tool: a small stdlib CLI (`python3 scripts/search_restaurants.py --city Boston`) that speaks MCP to `<tool>-mcp.<ns>.svc.cluster.local`, the same pattern as the `eventbridge` and `wiki-*-cli` skills in `rossoctl/examples`. To the agent it's just a local script. It needs no store-side changes in rossoctl. What's missing is on the agent side.

## The ladder

Ranked by how much rossoctl changes. Each rung is useful on its own; later rungs make the earlier ones automatic.

### Rung 0: no rossoctl change. Ship the wiring with the skill

The skill's tool dependencies are known at composition time, so the deployment can carry them.

- The registry declares the tools a skill needs (skillberry-store does this in `rossoctl-tools.json`).
- We ship a **deployment recipe** that sets `skills: [...]` and `MCP_URLS` (or the gateway `MCP_URL`) together. It can be a values snippet, a `rossoctl agents import` command, or a CLI helper that prints the right `MCP_URLS` for a namespace.
- It works today with `generic_agent`, or with any single-`MCP_URL` agent through the MCP Gateway.

*Cost:* docs and a helper. *Catch:* the binding is manual and lives outside the skill. The skill's own scripts still don't run.

### Rung 1: an example agent that runs skill scripts (`rossoctl/examples` only)

Make the agents we ship actually *use* skills:

- **`claude_agent`:** link each `SKILL_FOLDERS` entry into `~/.claude/skills/<name>`, and put `python3` on `PATH`. That's about 30 lines. Claude Code then discovers skills natively, with progressive disclosure, and runs their scripts. Proxy scripts for rossoctl tools just work, as do any other skill scripts.
- **`generic_agent`:** an opt-in `run_skill_script(skill, script, args)` tool, with these limits:
  - it runs only files under `<skill>/scripts/` (realpath check);
  - no shell;
  - a timeout and an output cap.
- Add `claude_agent` to the console presets. Recommend deploying script-running agents as a `Sandbox` workload.

*Cost:* example code only, no platform change. *Win:* one skill gives an agent N tools plus the instructions for them, with no `MCP_URL`. It's portable across namespaces.

### Rung 2: a "skill runner" agent type

Turn Rung 1 into a first-class, maintained agent in rossoctl:
- a hardened image (Claude Code or another script-capable runtime) that reads `SKILL_FOLDERS`, exposes skills natively, and runs their scripts;
- a console preset ("Skill agent"), a `Sandbox` workload by default, and a documented trust model.

*Cost:* a maintained image and a preset. No backend or CRD change.

### Rung 3: skills in the agent contract

Make skill support a contract, not a property of one image:
- Extend "Bring your own agent": an agent that declares skill support (for example a card capability, or a label) reads `SKILL_FOLDERS`, and loads SKILL.md with progressive disclosure.
- Optionally, have it advertise "runs skill scripts", so the console can warn when a script-bearing skill goes to an agent that can't run it.
- Provide a tiny reference library or sidecar for frameworks that want it: a skill loader plus a sandboxed script runner as a framework tool.

*Cost:* a docs and contract change, plus a reference implementation. Small backend work if the console checks the capability.

### Rung 4: rossoctl understands a skill's tool dependencies

Let rossoctl wire the tools for you:
- skills carry their required tools (an annotation such as `rossoctl.io/required-tools`, synced from the registry or read from the skill);
- agent create checks that those tools exist in the namespace (warn, or reject), and injects their URLs (`ROSSOCTL_TOOL_<T>_URL` or `MCP_URLS`), with the real Service ports;
- the console shows "Required tools ✓/✗" on skills and agents.

*Cost:* backend, UI and autosync changes behind a feature flag. This makes Rung 0 automatic.

### Rung 5: tools and skills in the CRDs

- `AgentRuntime.spec.skills` / `spec.requires.tools`, with the operator resolving them into `status.linkedSkills` / `status.linkedTools`.
- Auto-generated per-agent AuthBridge outbound routes for the required tools.
- Possibly a `Skill` CRD.

*Cost:* operator and CRD work. *Win:* skills and tool dependencies become declarative and reconciled, like everything else in rossoctl.

### Why this order

- **Rungs 0–1 change nothing in the platform.** They prove the value with what's already there.
- **Rung 2 is packaging.**
- **Rung 3 is the first real contract change,** but it's mostly docs.
- **Rungs 4–5 are where rossoctl starts reasoning about skills.** They only pay off once agents can actually use what they're given. That's why agent-side execution comes before dependency wiring, not after.

## Ask

We'd like feedback on two things:
1. **The ladder:** is the order right?
2. **A starting point:** we propose Rung 1 (the `claude_agent` skill linking, and the opt-in `generic_agent` runner) as a first PR to `rossoctl/examples`, with Rung 0's recipe in the docs.

A worked demo is ready. The `night-out-planner` skill gives one agent the weather tool and the reservation tool through two proxy scripts, with no `MCP_URL`. It runs on unmodified rossoctl `main` plus the Rung 1 agent change. Happy to walk through it.
