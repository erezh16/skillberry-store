import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { NpxInstallCommand, NPX_AGENTS, DEFAULT_NPX_AGENT } from './NpxInstallCommand';
import { skillsApi } from '@/services/api';

const COMMAND = 'npx skills add http://store.test/pub/pdf-forms -y -a claude-code';

describe('NpxInstallCommand', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the server-composed command verbatim', async () => {
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    // ClipboardCopy renders the command in a read-only input, not as text.
    expect(await screen.findByDisplayValue(COMMAND)).toBeTruthy();
  });

  it('asks the server for the default agent on first render', async () => {
    const spy = vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', DEFAULT_NPX_AGENT));
  });

  it('renders nothing when the store publishes no command', async () => {
    // The normal answer on a store with npx publishing off, on one that does
    // not know its own public URL, and on a superseded version of a skill.
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(null);
    const { container } = render(<NpxInstallCommand skillId="u1" />);
    await waitFor(() => expect(container.querySelector('[data-testid="npx-install"]')).toBeNull());
    expect(screen.queryByText(/Install with npx/)).toBeNull();
  });

  it('renders nothing when the request fails', async () => {
    vi.spyOn(skillsApi, 'npxInstallCommand').mockRejectedValue(new Error('403'));
    const { container } = render(<NpxInstallCommand skillId="u1" />);
    await waitFor(() => expect(container.querySelector('[data-testid="npx-install"]')).toBeNull());
  });

  it('re-fetches rather than rewriting the command when the agent changes', async () => {
    // The command string has exactly one author (tools/wellknown.py), so the
    // picker's only job is to tell the server which agent to pin.
    const spy = vi
      .spyOn(skillsApi, 'npxInstallCommand')
      .mockImplementation(async (_id: string, agent: string) =>
        COMMAND.replace('claude-code', agent)
      );
    render(<NpxInstallCommand skillId="u1" />);
    await screen.findByDisplayValue(COMMAND);

    fireEvent.click(screen.getByLabelText('Target agent'));
    fireEvent.click(await screen.findByText('Cursor'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', 'cursor'));
    expect(await screen.findByDisplayValue(/-a cursor$/)).toBeTruthy();
  });

  it('remembers the chosen agent for next time', async () => {
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    await screen.findByDisplayValue(COMMAND);

    fireEvent.click(screen.getByLabelText('Target agent'));
    fireEvent.click(await screen.findByText('Cursor'));

    await waitFor(() => expect(localStorage.getItem('sbs.npx.agent')).toBe('cursor'));
  });

  it('starts from the remembered agent', async () => {
    localStorage.setItem('sbs.npx.agent', 'codex');
    const spy = vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', 'codex'));
  });

  it('ignores a remembered agent that is no longer offered', async () => {
    localStorage.setItem('sbs.npx.agent', 'no-such-agent');
    const spy = vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', DEFAULT_NPX_AGENT));
  });

  it('offers claude-code first, since -a is never optional', () => {
    // `-y` with no detected agent installs into ~75 agent directories, so the
    // command always pins one.
    expect(NPX_AGENTS[0].id).toBe('claude-code');
    expect(DEFAULT_NPX_AGENT).toBe('claude-code');
    expect(new Set(NPX_AGENTS.map(a => a.id)).size).toBe(NPX_AGENTS.length);
  });

  it('surfaces the telemetry opt-out rather than hiding it in docs', async () => {
    // As prose next to the copy button, not as a prefix on the command: a
    // prefix protects one invocation, breaks in PowerShell, and misses every
    // later `npx skills update`.
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    expect(await screen.findByText(/DO_NOT_TRACK=1/)).toBeTruthy();
  });

  it('keeps the command itself a single portable line', async () => {
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(COMMAND);
    render(<NpxInstallCommand skillId="u1" />);
    const shown = (await screen.findByDisplayValue(COMMAND)) as HTMLInputElement;
    expect(shown.value.startsWith('npx skills add ')).toBe(true);
    expect(shown.value).not.toContain('DISABLE_TELEMETRY');
    expect(shown.value).not.toContain('DO_NOT_TRACK');
  });
});
