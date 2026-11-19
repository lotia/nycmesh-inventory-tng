/** Saves from the scan page held back, so a test sees them while sent. */
import type { Page } from "@playwright/test";

/**
 * Holds every save the page sends until the function it gives is called,
 * then lets them go on to the server.
 */
export async function holdSaves(page: Page): Promise<() => void> {
  let release = () => {};
  const held = new Promise<void>((done) => {
    release = done;
  });
  await page.route("**/api/transactions", async (route) => {
    await held;
    await route.continue();
  });
  return release;
}
