// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0
//
// "Install with npx" — an agent picker plus a copy button that emits the whole
// command for one skill. See docs/design/npx.md §4.3.2 and §4.8.
//
// This is not decoration. Two verified CLI behaviours make the naive command
// unpleasant, and both are invisible until you hit them: an SBS URL pre-selects
// *nothing* in the CLI's multiselect (skills are pre-selected only for skills.sh
// pack URLs), and `-y` with no detected agent installs into every supported
// agent's directory — around 75 of them in the user's tree. So the answer to
// "keep it simple" is not a documented flag incantation; it is a button that
// emits the flags for her.
//
// The command string itself is composed **server-side** and fetched whole
// (`GET /skills/{uuid}?fields=_npx_install&npx_agent=...`). Nothing here
// assembles or edits it: there is one definition of that string, in
// `tools/publish.py`, and the picker's only job is to tell the server which
// agent to pin. That is also why changing the agent re-fetches rather than
// rewriting the `-a` token locally.
//
// The telemetry opt-out is prose here rather than a prefix on the command, for
// the reasons in `npx_install_command`.

import { useEffect, useState } from 'react';
import {
  Alert,
  Card,
  CardBody,
  CardTitle,
  ClipboardCopy,
  ClipboardCopyVariant,
  FormGroup,
  MenuToggle,
  MenuToggleElement,
  Select,
  SelectList,
  SelectOption,
  Spinner,
  Text,
} from '@patternfly/react-core';
import { getAclMode } from '@/contexts/AuthContext';
import { skillsApi } from '@/services/api';

// The agents worth putting in a dropdown. The CLI accepts ~75; this is the
// short list people actually ask for, and anything else can be typed into the
// command by hand. Ordered with the most common first rather than
// alphabetically.
export const NPX_AGENTS = [
  { id: 'claude-code', label: 'Claude Code' },
  { id: 'cursor', label: 'Cursor' },
  { id: 'codex', label: 'Codex' },
  { id: 'windsurf', label: 'Windsurf' },
  { id: 'copilot', label: 'GitHub Copilot' },
  { id: 'agents', label: 'Generic (.agents/skills)' },
] as const;

export const DEFAULT_NPX_AGENT = NPX_AGENTS[0].id;

// Remembered so the picker defaults to whatever she used last — the command is
// something people copy repeatedly, and re-choosing the agent every time is the
// kind of small friction that makes a feature feel unfinished.
const AGENT_STORAGE_KEY = 'sbs.npx.agent';

function loadAgent(): string {
  try {
    const stored = localStorage.getItem(AGENT_STORAGE_KEY);
    if (stored && NPX_AGENTS.some(a => a.id === stored)) return stored;
  } catch {
    // localStorage disabled — best-effort only.
  }
  return DEFAULT_NPX_AGENT;
}

function storeAgent(agent: string): void {
  try {
    localStorage.setItem(AGENT_STORAGE_KEY, agent);
  } catch {
    // Ignored: the picker still works for this page load.
  }
}

interface Props {
  /** UUID or name of the skill to install. */
  skillId: string;
}

export function NpxInstallCommand({ skillId }: Props) {
  const [agent, setAgent] = useState<string>(loadAgent);
  const [isOpen, setIsOpen] = useState(false);
  const [command, setCommand] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    skillsApi
      .npxInstallCommand(skillId, agent)
      .then(value => {
        if (!cancelled) setCommand(value);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [skillId, agent]);

  if (loading) {
    return (
      <Card style={{ marginTop: '1rem' }}>
        <CardTitle>Install with npx</CardTitle>
        <CardBody>
          <Spinner size="md" aria-label="Loading install command" />
        </CardBody>
      </Card>
    );
  }

  // No command is the normal answer on a store that does not publish for npx,
  // on one that does not know its own public URL, and on a superseded version of
  // a skill. None of those is an error the reader can act on, so the section
  // simply is not there — which is why the component owns its own card.
  if (error || !command) {
    return null;
  }

  const onSelect = (_e: unknown, value: string | number | undefined) => {
    const next = String(value ?? DEFAULT_NPX_AGENT);
    setAgent(next);
    storeAgent(next);
    setIsOpen(false);
  };

  const selected = NPX_AGENTS.find(a => a.id === agent);

  return (
    <Card style={{ marginTop: '1rem' }} data-testid="npx-install">
      <CardTitle>Install with npx</CardTitle>
      <CardBody>
      <FormGroup label="Agent" fieldId="npx-agent" style={{ maxWidth: '20rem' }}>
        <Select
          id="npx-agent"
          isOpen={isOpen}
          selected={agent}
          onSelect={onSelect}
          onOpenChange={setIsOpen}
          toggle={(ref: React.Ref<MenuToggleElement>) => (
            <MenuToggle
              ref={ref}
              onClick={() => setIsOpen(!isOpen)}
              isExpanded={isOpen}
              aria-label="Target agent"
            >
              {selected?.label ?? agent}
            </MenuToggle>
          )}
        >
          <SelectList>
            {NPX_AGENTS.map(option => (
              <SelectOption key={option.id} value={option.id}>
                {option.label}
              </SelectOption>
            ))}
          </SelectList>
        </Select>
      </FormGroup>

      <ClipboardCopy
        isReadOnly
        isCode
        hoverTip="Copy install command"
        clickTip="Copied"
        variant={ClipboardCopyVariant.expansion}
        style={{ marginTop: '0.75rem' }}
      >
        {command}
      </ClipboardCopy>

      <Text component="small" style={{ display: 'block', marginTop: '0.5rem' }}>
        Paste this in your project. Nothing to install first — <code>npx</code>{' '}
        fetches the CLI. It reports a successful install to a third party; export{' '}
        <code>DO_NOT_TRACK=1</code> in your shell profile to opt out of that, here
        and on every later <code>npx skills update</code>.
      </Text>

      {/* Not a buried note: the command contains a credential, and that is the
          cost of a CLI that cannot authenticate. It belongs next to the copy
          button (§4.3.2) — which is also the right home for the telemetry
          opt-out, because this is the moment a reader is about to paste a
          credential-bearing URL. The command itself carries no
          `DISABLE_TELEMETRY=1` prefix: that would protect one invocation, break
          in PowerShell, and miss every later `npx skills update`. An exported
          `DO_NOT_TRACK=1` covers all of them (see npx_install_command).

          Shown by ACL mode rather than by inspecting the URL: under
          `standalone` the path segment is a capability token, under `disabled`
          it is just the slug and there is no secret to warn about. */}
      {getAclMode() === 'standalone' && (
        <Alert
          variant="warning"
          isInline
          isPlain
          title="This command contains an access token"
          style={{ marginTop: '0.5rem' }}
        >
          Anyone you share it with can read this one skill until the store&apos;s
          publish secret is rotated. It grants nothing else — but the install
          report above includes this URL, token and all, so{' '}
          <code>DO_NOT_TRACK=1</code> matters more here than on an open store.
        </Alert>
      )}
      </CardBody>
    </Card>
  );
}
