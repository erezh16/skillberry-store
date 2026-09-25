/**
 * The per-skill npx publish flag in the UI (docs/design/npx.md §5.12).
 *
 * Two things are being pinned: that the state is *visible* without opening the
 * edit modal, and — the one that would silently lose data — that saving an
 * unrelated edit carries the flag through instead of clearing it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { SkillDetailPage } from './SkillDetailPage';
import { skillsApi, toolsApi, snippetsApi } from '@/services/api';
import type { Skill } from '@/types';

function baseSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    uuid: 'u1',
    name: 'demo',
    description: 'A demo skill.',
    version: '1.0',
    tags: [],
    tools: [],
    snippets: [],
    ...overrides,
  } as Skill;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/skills/u1']}>
        <Routes>
          <Route path="/skills/:uuid" element={<SkillDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('SkillDetailPage npx publish flag', () => {
  beforeEach(() => {
    vi.spyOn(toolsApi, 'list').mockResolvedValue([]);
    vi.spyOn(snippetsApi, 'list').mockResolvedValue([]);
    // The install card fetches independently; keep it out of the way.
    vi.spyOn(skillsApi, 'npxInstallCommand').mockResolvedValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the flag as enabled when the skill is opted in', async () => {
    vi.spyOn(skillsApi, 'get').mockResolvedValue(baseSkill({ npx_publish: true }));
    renderPage();
    const label = await screen.findByTestId('npx-publish-state');
    expect(label.textContent).toBe('Enabled');
  });

  it.each([[false], [null], [undefined]])(
    'shows it as not enabled when the flag is %s',
    async value => {
      vi.spyOn(skillsApi, 'get').mockResolvedValue(
        baseSkill({ npx_publish: value as boolean | null })
      );
      renderPage();
      const label = await screen.findByTestId('npx-publish-state');
      // Unset and explicitly-off read the same to the user, because they mean
      // the same thing — this skill is not published.
      expect(label.textContent).toBe('Not enabled');
    }
  );

  it('carries the flag through an unrelated edit instead of clearing it', async () => {
    // `update` replaces the manifest, so a payload that omits the flag would
    // silently un-publish the skill on any save.
    vi.spyOn(skillsApi, 'get').mockResolvedValue(baseSkill({ npx_publish: true }));
    const update = vi
      .spyOn(skillsApi, 'update')
      .mockResolvedValue({ message: 'ok' } as never);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Update Skill|Save/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({ npx_publish: true });
  });

  it('sends the flag off when the checkbox is cleared', async () => {
    vi.spyOn(skillsApi, 'get').mockResolvedValue(baseSkill({ npx_publish: true }));
    const update = vi
      .spyOn(skillsApi, 'update')
      .mockResolvedValue({ message: 'ok' } as never);

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
    fireEvent.click(await screen.findByLabelText(/Allow installing this skill/));
    fireEvent.click(await screen.findByRole('button', { name: /Update Skill|Save/i }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toMatchObject({ npx_publish: false });
  });

  it('surfaces a refused update, which is how a missing role shows up', async () => {
    // Permission lives server-side: the store refuses the whole update for a
    // role without `skills:update`, and the UI shows why.
    vi.spyOn(skillsApi, 'get').mockResolvedValue(baseSkill({ npx_publish: false }));
    vi.spyOn(skillsApi, 'update').mockRejectedValue(
      new Error('no role grants \'update\' on \'skills\'')
    );

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
    fireEvent.click(await screen.findByLabelText(/Allow installing this skill/));
    fireEvent.click(await screen.findByRole('button', { name: /Update Skill|Save/i }));

    expect(await screen.findByText(/no role grants/)).toBeTruthy();
  });
});
