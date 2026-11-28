/**
 * The scan page's "Are you <name>?" alert, asked once the cart has been
 * unused a while (frontend/src/scan/CartForm.tsx): what only a real browser
 * shows, a double tap as a finger makes it on an answer that takes the
 * alert away. The rest of how it behaves is in ScanPage.names.test.tsx.
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { SAM } from "./scan-page";
import { clicksReaching, doubleTap, middleOverWhatMovesUp } from "./taps";

/** The scan page, Sam's name picked, its cart last used long ago, so it
 * asks. */
async function asked(page: Page) {
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: SAM });
  await page.evaluate(() => {
    const kept = JSON.parse(localStorage.getItem("inventory.cart.v2") ?? "{}");
    localStorage.setItem(
      "inventory.cart.v2",
      JSON.stringify({ ...kept, usedAt: 1 }),
    );
  });
  await page.reload();
  await expect(page.getByText(`Are you ${SAM}?`)).toBeVisible();
}

for (const answer of ["That's me", "Not me"]) {
  test(`a double tap on "${answer}" leaves the form below as it was, and focuses Your name (inventory-tng-m6dc)`, async ({
    page,
  }) => {
    await asked(page);
    const controls = "fieldset, input, select, label";
    // On the answer, where the form will move up to.
    const at = await middleOverWhatMovesUp(
      page.getByRole("button", { name: answer }),
      page.getByRole("alert").filter({ hasText: `Are you ${SAM}?` }),
      controls,
    );
    const form = page.locator("form").first();
    const kind = () =>
      form.evaluate((it) => new FormData(it as HTMLFormElement).get("kind"));
    const before = await kind();
    const name = page.getByLabel("Your name");
    // A tap on Your name changes neither its value nor the focus.
    const reached = await clicksReaching(form, controls);
    await doubleTap(page, at);
    await expect(page.getByText(`Are you ${SAM}?`)).toHaveCount(0);
    await expect(name).toBeFocused();
    // Once the space has gone, the form is where the finger was, the kinds'
    // radios or Your name: the second tap would have landed on it.
    await expect
      .poll(() =>
        page.evaluate(
          ({ x, y, controls }) =>
            document.elementFromPoint(x, y)?.closest(controls) != null,
          { ...at, controls },
        ),
      )
      .toBe(true);
    expect(await kind()).toBe(before);
    expect(await reached()).toBe(0);
    await expect(name).toBeFocused();
  });
}
