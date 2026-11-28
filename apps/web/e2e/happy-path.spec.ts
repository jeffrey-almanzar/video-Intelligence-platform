import AxeBuilder from '@axe-core/playwright';
import { expect, type Page, test } from '@playwright/test';
import { VIDEO } from './global-setup';

/**
 * Phase 5 exit criterion, as a user would do it: sign up, upload a video, watch it process,
 * explore the report, jump to timestamps, and ask a grounded question.
 */

const PASSWORD = 'correct horse battery staple';

async function currentSeconds(page: Page): Promise<number> {
  return page.locator('video').evaluate((v: HTMLVideoElement) => v.currentTime);
}

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  );
  expect(serious.map((v) => `${v.id}: ${v.nodes.length} × ${v.help}`)).toEqual([]);
}

test('a new user uploads a talk, explores the report and asks a grounded question', async ({
  page,
}) => {
  // Sign up (signed-out visitors land on sign-in).
  await page.goto('/');
  await expect(page).toHaveURL(/\/login/);
  await page.getByRole('link', { name: 'Create an account' }).click();
  await page.getByLabel('Email').fill(`e2e-${Date.now()}@test.local`);
  await page.getByLabel('Password').fill(PASSWORD);
  await expectAccessible(page);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('heading', { name: 'Your videos' })).toBeVisible();
  await expectAccessible(page);

  // Upload through the browser: parts go straight to storage, then processing starts.
  await page.locator('input[type="file"]').setInputFiles(VIDEO);
  await expect(page).toHaveURL(/\/videos\/[0-9a-f-]{36}$/, { timeout: 60_000 });
  await expect(page.getByRole('heading', { name: 'blender keynote excerpt' })).toBeVisible();

  // Live progress, then the report (fixture transcript + recorded model responses).
  await expect(
    page.getByRole('heading', { name: /Processing|Waiting for the upload/ }),
  ).toBeVisible();
  const tabs = page.getByRole('tablist', { name: 'Report sections' });
  await expect(tabs).toBeVisible({ timeout: 5 * 60_000 });
  await expect(page.getByRole('heading', { name: 'Summary' })).toBeVisible();
  await expectAccessible(page);

  // Jump to a takeaway's moment: the player seeks there.
  const takeaway = page
    .locator('ol li')
    .filter({ has: page.getByText('Why it matters:') })
    .first();
  const chip = takeaway.getByRole('button', { name: /^Play from/ });
  const label = (await chip.textContent())!;
  const [m, s] = label.split(':').map(Number) as [number, number];
  await chip.click();
  await expect.poll(() => currentSeconds(page)).toBeGreaterThanOrEqual(m * 60 + s - 1);

  // Keyboard: arrow keys move between tabs.
  await page.getByRole('tab', { name: 'Overview' }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Chapters' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('tabpanel').getByRole('heading').first()).toBeVisible();

  // Transcript search, then click a passage to play it.
  await page.getByRole('tab', { name: 'Transcript' }).click();
  await page.getByPlaceholder('Search the transcript').fill('blender');
  await expect(page.getByText(/\d+ matching passages?/)).toBeVisible();
  await expect(page.locator('mark').first()).toHaveText(/blender/i);

  // Ask a specific question; the answer streams in, cites moments that can be played, and is
  // labelled with how well its claims are grounded (Phase 6 verify step).
  await page.getByRole('tab', { name: 'Ask AI' }).click();
  const question = 'Why was Blender not invited to the metaverse events at SIGGRAPH?';
  await page.getByPlaceholder('Ask about this video').fill(question);
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  const answer = page.locator('[aria-live="polite"]').filter({ hasText: question });
  const cite = answer.getByRole('button', { name: /^Play from/ }).first();
  await expect(cite).toBeVisible({ timeout: 90_000 });
  await expect(
    answer.getByText(/^(Stated in the video|Includes AI interpretation)\b/),
  ).toBeVisible();
  await cite.click();
  await expect.poll(() => currentSeconds(page)).toBeGreaterThan(0);
  await expectAccessible(page);

  // Sign out ends the session.
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto('/');
  await expect(page).toHaveURL(/\/login/);
});
