/** axe-core via vitest-axe; colour contrast is checked in the browser E2E (jsdom has no layout). */
import { configureAxe } from 'vitest-axe';

export const axe = configureAxe({
  rules: {
    'color-contrast': { enabled: false },
    // Testing-library mounts components outside a full page; landmarks are asserted on full-page renders.
    region: { enabled: false },
  },
});

export async function violations(container: Element): Promise<string[]> {
  const res = await axe(container);
  return res.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`);
}
