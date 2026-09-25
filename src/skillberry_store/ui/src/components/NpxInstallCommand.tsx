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
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Card,
  CardBody,
  CardTitle,
  ClipboardCopy,
  ClipboardCopyVariant,
  FormGroup,
  List,
  ListItem,
  MenuToggle,
  MenuToggleElement,
  Select,
  SelectList,
  SelectOption,
  Spinner,
  Switch,
  Text,
  Tooltip,
} from '@patternfly/react-core';
import { ExternalLinkAltIcon } from '@patternfly/react-icons';
import { getAclMode } from '@/contexts/AuthContext';
import { skillsApi } from '@/services/api';
import type { NpxPublishMode, Skill } from '@/types';

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

// `npx` is not installed on its own — it ships with Node.js, and the skills CLI
// requires a recent one (`engines: node >= 22.20.0` in its package.json), so a
// reader on an older Node hits a confusing failure rather than a version error.
// Point at the official downloads page rather than a package manager, since the
// right install route differs per platform.
export const NODE_DOWNLOAD_URL = 'https://nodejs.org/en/download';
export const NODE_MIN_VERSION = '22.20';

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

/**
 * Is npx install ON for this skill right now, given the store's master switch?
 *
 * Under `selective` the skill's own flag decides. Under `true`/`false` the store
 * decides for every skill and the flag is ignored — so the switch must show the
 * *store's* value there, not the flag. Showing the raw flag was a real bug: a
 * store set to `true` displayed "not published" beside a working install command.
 */
export function effectivePublishState(
  mode: NpxPublishMode | null,
  flag: boolean | null
): boolean {
  if (mode === 'true') return true;
  if (mode === 'false') return false;
  return flag === true;
}

/**
 * Wrap `control` in a hover tooltip when there is a reason it is locked.
 *
 * A `<span>` wrapper rather than the tooltip on the control itself: a disabled
 * input does not reliably emit mouse events, so a tooltip attached straight to it
 * never opens. `entryDelay={0}` because this explains why a control is
 * unclickable — the reader is already hovering to find out.
 */
function withLockTooltip(control: React.ReactElement, reason: string | null) {
  if (!reason) return control;
  return (
    <Tooltip content={reason} entryDelay={0}>
      <span data-testid="npx-publish-lock">{control}</span>
    </Tooltip>
  );
}

interface Props {
  /** The skill to install. The whole object, because toggling the publish flag
   *  writes the manifest back and `update` replaces it — a partial payload would
   *  clear every field it omitted. */
  skill: Skill;
}

export function NpxInstallCommand({ skill }: Props) {
  const skillId = skill.uuid;
  const queryClient = useQueryClient();
  const [agent, setAgent] = useState<string>(loadAgent);
  const [isOpen, setIsOpen] = useState(false);
  const [command, setCommand] = useState<string | null>(null);
  const [mode, setMode] = useState<NpxPublishMode | null>(null);
  const [flag, setFlag] = useState<boolean | null>(null);
  const [editable, setEditable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    skillsApi
      .npxState(skillId, agent)
      .then(state => {
        if (cancelled) return;
        setCommand(state.command);
        setMode(state.mode);
        setFlag(state.flag);
        setEditable(state.editable);
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
  }, [skillId, agent, skill.npx_publish]);

  // Writes the manifest back with the flag flipped. Every field goes along,
  // because `update` replaces the manifest rather than patching it.
  const toggleMutation = useMutation({
    mutationFn: (next: boolean) =>
      skillsApi.update(skill.name, {
        uuid: skill.uuid,
        name: skill.name,
        version: skill.version,
        description: skill.description,
        tags: skill.tags,
        tool_uuids: skill.tool_uuids ?? skill.tools?.map(t => t.uuid) ?? [],
        snippet_uuids: skill.snippet_uuids ?? skill.snippets?.map(s => s.uuid) ?? [],
        state: skill.state,
        extra: skill.extra,
        npx_publish: next,
      } as unknown as Skill),
    onSuccess: (_data, next) => {
      setFlag(next);
      setToggleError(null);
      // The command appears or disappears with the flag, so re-read rather than
      // guess, and let the page's own copy of the skill refresh too.
      skillsApi.npxState(skillId, agent).then(state => setCommand(state.command));
      queryClient.invalidateQueries({ queryKey: ['skills', skillId] });
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
    onError: (e: Error) => setToggleError(e.message || 'Failed to update skill'),
  });

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

  // A failed read is the only case with nothing to show. Note that a *missing
  // command* is not: the switch below is what turns one on, so hiding the card
  // when there is no command would hide the only control that could create one.
  if (error || mode === null) {
    return null;
  }

  const published = effectivePublishState(mode, flag);

  // Why the switch cannot be changed, or `null` when it can. Short and
  // non-technical: it is read on hover, not studied. Kept separate from
  // `switchDisabled` so an in-flight save disables the control without
  // claiming a reason that is not true.
  const lockedReason =
    mode === 'true'
      ? 'npx publish enabled globally'
      : mode === 'false'
        ? 'npx publish disabled globally'
        : !editable
          ? 'You are not allowed to change this setting'
          : null;
  const switchDisabled = lockedReason !== null || toggleMutation.isPending;

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
      {/* The control comes first: it is what decides whether there is anything
          else on this card at all.
          
          The label spells the state out — "…: YES" / "…: NO" — because a
          disabled switch is hard to read as on or off from its position alone,
          which is exactly how it was reported. `aria-label` stays constant so
          the accessible name is stable; screen readers announce the checked
          state themselves.

          One computed `label` rather than PatternFly's `label`/`labelOff` pair:
          it renders both spans either way and shows one by CSS, so deriving the
          text from state means whichever span is visible is correct, with no
          second string to keep in step. */}
      {withLockTooltip(
        <Switch
          id="npx-publish-switch"
          aria-label="Publish this skill with npx"
          label={`Publish this skill with npx: ${published ? 'YES' : 'NO'}`}
          isChecked={published}
          isDisabled={switchDisabled}
          onChange={(_e, checked) => toggleMutation.mutate(checked)}
        />,
        lockedReason
      )}

      {toggleError && (
        <Alert
          variant="danger"
          isInline
          isPlain
          title="Could not change the setting"
          style={{ marginTop: '0.5rem' }}
        >
          {toggleError}
        </Alert>
      )}

      {/* Switched off, the card is just the switch: no agent picker, no command,
          no notes. There is nothing to install, so anything else is clutter.
          A skill that IS published but still has no command is a different
          case — something is misconfigured, and saying so is worth the line. */}
      {published && !command && (
        <Text component="small" style={{ display: 'block', marginTop: '0.75rem' }}>
          No install command available — the store may not know its own public URL
          (<code>SBS_PUBLIC_URL</code>), or this is a superseded version of the
          skill.
        </Text>
      )}

      {published && command && (
        <>
      <FormGroup
        label="Agent"
        fieldId="npx-agent"
        style={{ maxWidth: '20rem', marginTop: '1rem' }}
      >
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

      {/* One sentence per item: these are three unrelated matters — where to run
          it, what you need first, and what it reports — and they read as a wall
          of text when run together. */}
      <List isPlain style={{ marginTop: '0.75rem', fontSize: '0.875rem' }}>
        <ListItem>Paste this in your project.</ListItem>
        <ListItem>
          Nothing to install first — <code>npx</code> fetches the skills CLI and
          caches it. It ships with Node.js{' '}
          <Button
            variant="link"
            isInline
            component="a"
            href={NODE_DOWNLOAD_URL}
            target="_blank"
            rel="noopener noreferrer"
            icon={<ExternalLinkAltIcon />}
            iconPosition="right"
          >
            (install Node {NODE_MIN_VERSION} or newer)
          </Button>
          .
        </ListItem>
        <ListItem>
          <code>npx</code> reports a successful install to a third party. Export{' '}
          <code>DO_NOT_TRACK=1</code> in your shell profile to opt out of that —
          here, and on every later <code>npx skills update</code>.
        </ListItem>
      </List>

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
          publish seed is rotated. It grants nothing else — but the install
          report above includes this URL, token and all, so{' '}
          <code>DO_NOT_TRACK=1</code> matters more here than on an open store.
        </Alert>
      )}
        </>
      )}
      </CardBody>
    </Card>
  );
}
