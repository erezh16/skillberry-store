/**
 * Contrast regression guards for `global.css`.
 *
 * Both bugs these cover were reported from a real browser and are invisible to a
 * component test: jsdom does not apply the stylesheet, so nothing else in the
 * suite can see them. Reading the CSS is unusual but it is the only level at
 * which a "white text on a white card" defect can be pinned.
 *
 * The shared cause is a blanket element selector that forces a colour without
 * knowing what is painted behind the element.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { join } from 'path';

const css = readFileSync(join(__dirname, 'global.css'), 'utf8');

/** The body of the first rule whose selector list matches `selector` exactly. */
function ruleBody(selector: string): string {
  const pattern = new RegExp(
    `(^|\\})\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*\\{([^}]*)\\}`,
    'm'
  );
  const match = css.match(pattern);
  expect(match, `no rule found for selector \`${selector}\``).toBeTruthy();
  return match![2];
}

describe('global.css contrast', () => {
  it('does not paint inline code with the light token', () => {
    // Reported: `<code>npx</code>` and `<code>DO_NOT_TRACK=1</code>` in body text
    // on a white card were white on white, readable only by selecting them.
    const body = ruleBody('code');
    expect(body).toContain('color: inherit');
    expect(body).not.toContain('--pf-v5-global--Color--light-100');
  });

  it('still paints code on the dark pre background with the light token', () => {
    // The fix must not swing the other way: `pre` sets a dark background, so its
    // text — with or without a `code` child — needs the light colour.
    const rule = css.match(/\npre,\npre code \{([^}]*)\}/);
    expect(rule, 'no `pre, pre code` rule found').toBeTruthy();
    expect(rule![1]).toContain('--pf-v5-global--Color--light-100');
  });

  it('restores light text on tooltips, which PatternFly paints dark', () => {
    // Reported: the copy button's hover tooltip was an empty black bubble — its
    // text was there, painted #151515 on #151515 by the blanket `div`/`span`
    // rule.
    expect(css).toMatch(/\.pf-v5-c-tooltip__content\s*\*?[\s,]/);
    const tooltipRule = css.slice(css.indexOf('.pf-v5-c-tooltip,'));
    expect(tooltipRule).toContain('--pf-v5-global--Color--light-100');
  });

  it('applies the tooltip exception after the blanket rule that causes it', () => {
    // Equal specificity, so source order decides the winner.
    expect(css.indexOf('p, span, div, li, td, th, label')).toBeLessThan(
      css.indexOf('.pf-v5-c-tooltip,')
    );
  });

  it('covers descendants of a tooltip, not only its container', () => {
    // The blanket rule matches any `span`/`div` inside the bubble too, so the
    // exception has to reach them or nested markup stays invisible.
    const tooltipRule = css.slice(css.indexOf('.pf-v5-c-tooltip,'));
    expect(tooltipRule.slice(0, tooltipRule.indexOf('}'))).toContain(
      '.pf-v5-c-tooltip__content *'
    );
  });
});
