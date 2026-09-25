/**
 * The per-skill npx publish flag, as seen from the skill page.
 *
 * The switch itself lives in the "Install with npx" card and is tested in
 * `NpxInstallCommand.test.tsx`. What is pinned here is the one thing the *page*
 * can still get wrong: `update` replaces the manifest, so an unrelated edit that
 * omitted the flag would silently un-publish the skill.
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

describe('SkillDetailPage and the npx publish flag', () => {
  beforeEach(() => {
    vi.spyOn(toolsApi, 'list').mockResolvedValue([]);
    vi.spyOn(snippetsApi, 'list').mockResolvedValue([]);
    // The card fetches independently; keep it out of the way here.
    vi.spyOn(skillsApi, 'npxState').mockResolvedValue({
      command: null,
      mode: 'selective',
      flag: true,
      editable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([[true], [false], [null]])(
    'carries a flag of %s through an unrelated edit',
    async value => {
      vi.spyOn(skillsApi, 'get').mockResolvedValue(
        baseSkill({ npx_publish: value as boolean | null })
      );
      const update = vi
        .spyOn(skillsApi, 'update')
        .mockResolvedValue({ message: 'ok' } as never);

      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /^Edit$/ }));
      fireEvent.click(await screen.findByRole('button', { name: /Update Skill|Save/i }));

      await waitFor(() => expect(update).toHaveBeenCalled());
      // `null` and `false` both mean "not published", so either may be sent for
      // an unset flag — what must not happen is a payload with the flag missing,
      // or a `true` silently becoming `false`.
      expect(update.mock.calls[0][1]).toMatchObject({ npx_publish: value === true });
    }
  );

  it('no longer renders a second control for the flag', async () => {
    // One control, in the card. Two would disagree the moment either went stale.
    vi.spyOn(skillsApi, 'get').mockResolvedValue(baseSkill({ npx_publish: true }));
    renderPage();
    await screen.findByRole('button', { name: /^Edit$/ });
    expect(screen.queryByTestId('npx-publish-state')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    await screen.findByLabelText(/Name/i);
    expect(screen.queryByLabelText(/Allow installing this skill/)).toBeNull();
  });
});
