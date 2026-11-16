/**
 * The assistant window's width is bounded by the viewport, not by a fixed cap.
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

async function opened(page, context, viewport) {
  await page.setViewportSize(viewport);
  await mockChatServer(context, {});
  const assistant = new AssistantPage(page);
  await assistant.logIn();
  await assistant.open();
  return assistant;
}

const width = async (assistant) => (await assistant.panel.boundingBox()).width;

async function dragWestHandleTo(assistant, page, targetWidth) {
  const handle = assistant.host.locator('.resize-w');
  const hb = await handle.boundingBox();
  const start = await width(assistant);
  const x = hb.x + hb.width / 2;
  const y = hb.y + hb.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - (targetWidth - start), y, { steps: 8 });
  await page.mouse.up();
}

test.describe('widget resizes past 640px', () => {
  test('resizes past 640 on a wide screen', async ({ page, context }) => {
    const assistant = await opened(page, context, { width: 1600, height: 900 });
    await dragWestHandleTo(assistant, page, 1300);
    expect(await width(assistant)).toBeGreaterThanOrEqual(1200);
  });

  test('resizes past 640 with the history panel open', async ({ page, context }) => {
    const assistant = await opened(page, context, { width: 1600, height: 900 });
    await assistant.historyButton.click();
    await expect(assistant.historyPanel).toBeVisible();
    await dragWestHandleTo(assistant, page, 1300);
    expect(await width(assistant)).toBeGreaterThanOrEqual(1200);
  });

  test('resizes past 640 but stays within the viewport on a narrow desktop', async ({ page, context }) => {
    const assistant = await opened(page, context, { width: 800, height: 900 });
    await dragWestHandleTo(assistant, page, 790);
    const w = await width(assistant);
    expect(w).toBeGreaterThan(640);
    expect(w).toBeLessThanOrEqual(768);
  });

  test('resizes past 640: a saved wider width is clamped when the window shrinks', async ({ page, context }) => {
    const assistant = await opened(page, context, { width: 1600, height: 900 });
    await dragWestHandleTo(assistant, page, 1300);
    await page.setViewportSize({ width: 1000, height: 900 });
    await expect.poll(() => width(assistant)).toBeLessThanOrEqual(968);
  });

  test('resizes past 640 does not apply on a phone: the widget is full screen', async ({ page, context }) => {
    const assistant = await opened(page, context, { width: 400, height: 800 });
    const box = await assistant.panel.boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(399);
    expect(box.height).toBeGreaterThanOrEqual(799);
  });
});
