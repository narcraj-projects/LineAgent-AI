import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';

test('real local catalog, PDF bytes, page mapping and measured evaluation', async ({ page, request }) => {
  await request.delete('/api/connection');
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Clarity, backed by evidence.' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect live services' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Investigate alarm' })).toBeDisabled();
  await expect(page.getByText('20', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../tmp/dashboard-desktop.png', fullPage: true });
  const catalog = await (await request.get('/api/catalog')).json();
  const file = catalog.pdf_files[0];
  const response = await request.get(`/api/manuals/${file.filename}`);
  expect(response.headers()['content-type']).toBe('application/pdf');
  expect(createHash('sha256').update(await response.body()).digest('hex')).toBe(file.sha256);
  await page.getByRole('button', { name: /Manual library/ }).click();
  await page.getByRole('button').filter({ hasText: 'SYN-SEC-007' }).click();
  await expect(page.getByTitle('Synthetic manual preview')).toHaveAttribute('src', /\/pages\/7\.png/);
  await expect(page.getByText(/logical p.1 · physical PDF p.7/)).toBeVisible();
  await page.screenshot({ path: '../tmp/dashboard-manuals.png', fullPage: true });
  await page.getByRole('button', { name: 'Evaluation lab' }).click();
  await expect(page.getByRole('heading', { name: 'Retrieval comparison' })).toBeVisible();
  await expect(page.getByRole('table').getByRole('row')).toHaveCount(5);
  await expect(page.getByText('Unknown', { exact: true })).toHaveCount(4);
  await page.screenshot({ path: '../tmp/dashboard-evaluation.png', fullPage: true });
});

test('responsive mobile navigation and manual preview have no page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Clarity, backed by evidence.' })).toBeVisible();
  await expect(page.getByText('20', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: '../tmp/dashboard-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('button', { name: /Manual library/ }).click();
  await expect(page.getByTitle('Synthetic manual preview')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('live PDF-backed combined diagnosis, graph and supervisor snapshot; no feedback write', async ({ page, request }) => {
  test.skip(process.env.LINEMEDIC_LIVE_BROWSER !== '1', 'Opt in to actual configured providers with LINEMEDIC_LIVE_BROWSER=1.');
  await page.goto('/');
  await page.getByRole('button', { name: 'Connect live services' }).click();
  await expect(page.getByRole('button', { name: 'Disconnect services' })).toBeVisible({ timeout: 60_000 });
  await page.getByLabel('Retrieval strategy').selectOption('combined');
  await page.getByLabel('Alarm code').fill('SYN-ALM-004');
  await page.getByRole('button', { name: 'Investigate alarm' }).click();
  await expect(page.getByRole('heading', { name: 'SYN-FIX-004' })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTitle('PDF evidence SYN-SEC-007')).toHaveAttribute('src', /\/pages\/7\.png/);
  const status = await (await request.get('/api/status')).json();
  expect(status.connected).toBe(true); expect(status.pdf_mode).toBe(true);
  expect(status.vector_namespace).toContain('-pdf-');
  test.info().annotations.push({ type: 'actual-graph-mode', description: `${status.graph_provider}:${status.graph_mode}` });
  await page.screenshot({ path: '../tmp/dashboard-live-diagnosis.png', fullPage: true });
  await page.getByRole('button', { name: 'Graph path' }).click();
  await expect(page.getByText('VERIFIED RELATIONSHIPS')).toBeVisible();
  await page.getByRole('button', { name: /Failure mode SYN-FM-004/ }).click();
  await page.screenshot({ path: '../tmp/dashboard-live-graph.png', fullPage: true });
  await page.getByRole('button', { name: 'Supervisor', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'See what the record says.' })).toBeVisible();
  await expect(page.getByText('51', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../tmp/dashboard-live-supervisor.png', fullPage: true });
});
