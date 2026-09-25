import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  NpxInstallCommand,
  NPX_AGENTS,
  DEFAULT_NPX_AGENT,
  NODE_DOWNLOAD_URL,
  NODE_MIN_VERSION,
  effectivePublishState,
} from './NpxInstallCommand';
import { skillsApi } from '@/services/api';
import type { NpxPublishMode, Skill } from '@/types';

const COMMAND = 'npx skills add http://store.test/pub/pdf-forms -y -a claude-code';

const SKILL = {
  uuid: 'u1',
  name: 'pdf-forms',
  description: 'Fill forms.',
  tags: [],
  tool_uuids: [],
  snippet_uuids: [],
} as unknown as Skill;

function state(over: Partial<Awaited<ReturnType<typeof skillsApi.npxState>>> = {}) {
  return {
    command: COMMAND,
    mode: 'selective' as NpxPublishMode,
    flag: true,
    editable: true,
    ...over,
  };
}

function renderCard(skill: Skill = SKILL) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NpxInstallCommand skill={skill} />
    </QueryClientProvider>
  );
}

const theSwitch = () =>
  screen.getByLabelText('Publish this skill with npx') as HTMLInputElement;

describe('effectivePublishState', () => {
  // The store-wide value wins under `true`/`false`; only `selective` defers to the
  // skill. Rendering the raw flag instead showed "not published" beside a working
  // install command on a store set to `true` — the reported bug.
  it.each([
    ['true', true, true],
    ['true', false, true],
    ['true', null, true],
    ['false', true, false],
    ['false', false, false],
    ['false', null, false],
    ['selective', true, true],
    ['selective', false, false],
    ['selective', null, false],
  ] as Array<[NpxPublishMode, boolean | null, boolean]>)(
    'mode=%s flag=%s -> %s',
    (mode, flag, expected) => {
      expect(effectivePublishState(mode, flag)).toBe(expected);
    }
  );
});

describe('NpxInstallCommand', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the server-composed command verbatim', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    // ClipboardCopy renders the command in a read-only input, not as text.
    expect(await screen.findByDisplayValue(COMMAND)).toBeTruthy();
  });

  it('asks the server for the default agent on first render', async () => {
    const spy = vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', DEFAULT_NPX_AGENT));
  });

  // ── the switch ──────────────────────────────────────────────────────────
  it('shows the switch on and editable under selective when the flag is set', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state({ flag: true }));
    renderCard();
    await waitFor(() => expect(theSwitch().checked).toBe(true));
    expect(theSwitch().disabled).toBe(false);
  });

  it('shows the switch off under selective when the flag is not set', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
      state({ flag: false, command: null })
    );
    renderCard();
    await waitFor(() => expect(theSwitch().checked).toBe(false));
    expect(theSwitch().disabled).toBe(false);
  });

  it.each([
    [true, 'YES'],
    [false, 'NO'],
  ])('spells the state out in the label: %s -> %s', async (on, word) => {
    // A disabled switch is hard to read as on or off from its position alone,
    // which is how this was reported.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
      state({ flag: on, command: on ? COMMAND : null })
    );
    renderCard();
    // PatternFly renders the label in both its on and off spans and shows one by
    // CSS; the text is computed from state, so every copy reads correctly.
    const labels = await screen.findAllByText(`Publish this skill with npx: ${word}`);
    expect(labels.length).toBeGreaterThan(0);
  });

  it('updates the label when the switch is toggled', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state({ flag: false, command: null }));
    vi.spyOn(skillsApi, 'update').mockResolvedValue({ message: 'ok' } as never);
    renderCard();
    await screen.findAllByText('Publish this skill with npx: NO');
    fireEvent.click(theSwitch());
    expect(
      (await screen.findAllByText('Publish this skill with npx: YES')).length
    ).toBeGreaterThan(0);
  });

  it.each([
    ['true' as NpxPublishMode, true],
    ['false' as NpxPublishMode, false],
  ])('greys the switch out under master=%s and shows that value', async (mode, on) => {
    // The per-skill flag is deliberately the opposite of the store's, to prove
    // the store's value is what is rendered.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
      state({ mode, flag: !on, command: on ? COMMAND : null })
    );
    renderCard();
    await waitFor(() => expect(theSwitch().disabled).toBe(true));
    expect(theSwitch().checked).toBe(on);
    expect(
      screen.getAllByText(`Publish this skill with npx: ${on ? 'YES' : 'NO'}`).length
    ).toBeGreaterThan(0);
  });

  it('greys the switch out when the caller may not update skills', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state({ editable: false }));
    renderCard();
    await waitFor(() => expect(theSwitch().disabled).toBe(true));
  });

  // ── the lock reason is a hover bubble, not a line of prose ───────────────
  it.each([
    ['true' as NpxPublishMode, true, true, 'npx publish enabled globally'],
    ['false' as NpxPublishMode, false, true, 'npx publish disabled globally'],
    ['selective' as NpxPublishMode, true, false, 'You are not allowed to change'],
  ])(
    'explains a locked switch on hover (mode=%s editable=%s)',
    async (mode, flag, editable, expected) => {
      vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
        state({ mode, flag, editable, command: null })
      );
      renderCard();
      const wrapper = await screen.findByTestId('npx-publish-lock');
      fireEvent.mouseEnter(wrapper);
      expect(await screen.findByText(new RegExp(expected))).toBeTruthy();
    }
  );

  it('adds no hover bubble when the switch can actually be changed', async () => {
    // Nothing to explain, so nothing to get in the way.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    await screen.findByDisplayValue(COMMAND);
    expect(screen.queryByTestId('npx-publish-lock')).toBeNull();
  });

  // ── switched off, the card is only the switch ────────────────────────────
  it('hides the rest of the card when the switch is off', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
      state({ flag: false, command: null })
    );
    const { container } = renderCard();
    await screen.findAllByText('Publish this skill with npx: NO');

    expect(screen.queryByLabelText('Target agent')).toBeNull();      // no picker
    expect(container.querySelector('input[readonly]')).toBeNull();   // no command
    expect(container.querySelectorAll('li').length).toBe(0);         // no notes
    expect(screen.queryByText(/DO_NOT_TRACK/)).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();                   // no Node link
  });

  it('still explains a published skill that has no command', async () => {
    // Not the same case: something is misconfigured, and saying so is useful.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state({ flag: true, command: null }));
    renderCard();
    expect(await screen.findByText(/No install command available/)).toBeTruthy();
  });

  it('writes the whole manifest back when toggled, not just the flag', async () => {
    // `update` replaces the manifest, so a partial payload would clear the rest.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(
      state({ flag: false, command: null })
    );
    const update = vi
      .spyOn(skillsApi, 'update')
      .mockResolvedValue({ message: 'ok' } as never);

    renderCard({ ...SKILL, description: 'Fill forms.', version: '2.0' } as Skill);
    await waitFor(() => expect(theSwitch().checked).toBe(false));
    fireEvent.click(theSwitch());

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({
      name: 'pdf-forms',
      description: 'Fill forms.',
      version: '2.0',
      npx_publish: true,
    });
  });

  it('surfaces a refused toggle instead of silently reverting', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state({ flag: false, command: null }));
    vi.spyOn(skillsApi, 'update').mockRejectedValue(
      new Error("no role grants 'update' on 'skills'")
    );
    renderCard();
    await waitFor(() => expect(theSwitch().checked).toBe(false));
    fireEvent.click(theSwitch());
    expect(await screen.findByText(/no role grants/)).toBeTruthy();
  });

  // ── the rest of the card ────────────────────────────────────────────────
  it('renders nothing when the state cannot be read', async () => {
    vi.spyOn(skillsApi, 'npxState').mockRejectedValue(new Error('403'));
    const { container } = renderCard();
    await waitFor(() =>
      expect(container.querySelector('[data-testid="npx-install"]')).toBeNull()
    );
  });

  it('re-fetches rather than rewriting the command when the agent changes', async () => {
    // The command string has exactly one author (tools/publish.py), so the
    // picker's only job is to tell the server which agent to pin.
    const spy = vi
      .spyOn(skillsApi, 'npxState')
      .mockImplementation(async (_id: string, agent: string) =>
        state({ command: COMMAND.replace('claude-code', agent) })
      );
    renderCard();
    await screen.findByDisplayValue(COMMAND);

    fireEvent.click(screen.getByLabelText('Target agent'));
    fireEvent.click(await screen.findByText('Cursor'));

    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', 'cursor'));
    expect(await screen.findByDisplayValue(/-a cursor$/)).toBeTruthy();
  });

  it('remembers the chosen agent for next time', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    await screen.findByDisplayValue(COMMAND);
    fireEvent.click(screen.getByLabelText('Target agent'));
    fireEvent.click(await screen.findByText('Cursor'));
    await waitFor(() => expect(localStorage.getItem('sbs.npx.agent')).toBe('cursor'));
  });

  it('starts from the remembered agent', async () => {
    localStorage.setItem('sbs.npx.agent', 'codex');
    const spy = vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', 'codex'));
  });

  it('ignores a remembered agent that is no longer offered', async () => {
    localStorage.setItem('sbs.npx.agent', 'no-such-agent');
    const spy = vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    await waitFor(() => expect(spy).toHaveBeenCalledWith('u1', DEFAULT_NPX_AGENT));
  });

  it('offers claude-code first, since -a is never optional', () => {
    // `-y` with no detected agent installs into ~75 agent directories.
    expect(NPX_AGENTS[0].id).toBe('claude-code');
    expect(DEFAULT_NPX_AGENT).toBe('claude-code');
    expect(new Set(NPX_AGENTS.map(a => a.id)).size).toBe(NPX_AGENTS.length);
  });

  it('breaks the guidance into one item per matter', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    const { container } = renderCard();
    await screen.findByDisplayValue(COMMAND);
    const items = container.querySelectorAll('li');
    expect(items.length).toBe(3);
    expect(items[0].textContent).toMatch(/^Paste this in your project\.$/);
    expect(items[1].textContent).toMatch(/Nothing to install first/);
    // "npx reports…", not "It reports…" — the subject was ambiguous.
    expect(items[2].textContent).toMatch(/^npx reports a successful install/);
  });

  it('links to the Node download, since npx is not installed on its own', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    const link = (await screen.findByRole('link', {
      name: new RegExp(`install Node ${NODE_MIN_VERSION}`),
    })) as HTMLAnchorElement;
    expect(link.href).toBe(NODE_DOWNLOAD_URL);
    expect(link.target).toBe('_blank');
    expect(link.rel).toContain('noopener');
  });

  it('surfaces the telemetry opt-out rather than hiding it in docs', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    expect(await screen.findByText(/DO_NOT_TRACK=1/)).toBeTruthy();
  });

  it('keeps the command itself a single portable line', async () => {
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue(state());
    renderCard();
    const shown = (await screen.findByDisplayValue(COMMAND)) as HTMLInputElement;
    expect(shown.value.startsWith('npx skills add ')).toBe(true);
    expect(shown.value).not.toContain('DISABLE_TELEMETRY');
    expect(shown.value).not.toContain('DO_NOT_TRACK');
  });
});
