/**
 * What the administrator pages show from a field's value stays right when
 * the browser puts the value back by itself, on Back (inventory-tng-r86h):
 * Chromium does so after the page's script has run, with no input or
 * change event, so each follower checks again on pageshow
 * (admin/follow-fields.ts).
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable } from "./own-rows";

/** Leaves the page for another, and comes back to it with Back. */
async function awayAndBack(page: Page): Promise<void> {
  await page.goto("/admin");
  await page.goBack();
}

test("the unit of the item picked is still beside its box after Back", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/admin/counts");
  const adjust = page.locator("#adjust");
  const item = adjust.getByLabel(/^Item/);
  const howMuch = adjust.getByRole("textbox", { name: /^How much/ });
  await item.selectOption({ label: `${cable} (m)` });
  await expect(howMuch).toHaveAccessibleDescription("m");
  await awayAndBack(page);
  await expect(item.locator("option:checked")).toHaveText(`${cable} (m)`);
  await expect(howMuch).toHaveAccessibleDescription("m");
});

test("the Search the web link still follows what is typed after Back", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const manufacturer = page.getByLabel(/^Manufacturer/);
  await manufacturer.fill("Ubiquiti");
  const link = page.getByRole("link", { name: "Search the web" });
  await expect(link).toBeVisible();
  await awayAndBack(page);
  await expect(manufacturer).toHaveValue("Ubiquiti");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute(
    "href",
    "https://duckduckgo.com/?q=Ubiquiti",
  );
});

// The other followers' pages need rows no browser fixture makes (a length
// check due, a printed length, a retired area emptied while counted, a
// batch refused as recorded already), so each is added to a page with
// the fields' names, types and the attributes its follower reads as its
// template writes them, and the page is sent with it every time, as the
// browser puts back fields only into the same markup.
// Exact markup on purpose: the fields a follower reads, as the server sends them.
const FOLLOWERS = `
  <form>
    <input name="left" id="left-1" aria-label="Left" value="3">
    <button type="button">Set to <span data-follows="left-1" data-unit="m">it</span></button>
    <select name="found_package_id" aria-label="Package">
      <option value="">None</option><option value="1" data-hinted>Spool</option>
    </select>
    <input name="found_left" aria-label="Found" aria-describedby="hint-found_left">
    <span id="hint-found_left" data-hint-for="found_package_id">Read the length</span>
    <input id="count-1" name="count" aria-label="Count" value="305" data-was="305">
    <p id="count-1-was" hidden>Was 305 m</p>
    <input name="moved" aria-label="Moved" value="1" data-adds-to="k" data-unset="1">
    <p data-total="k" hidden><span role="status" aria-atomic="true">After saving: <span data-total-amount>1</span> m</span></p>
  </form>`;

test("each other follower still shows what is in its field after Back", async ({
  page,
}) => {
  await page.route("**/admin/items", async (route) => {
    const response = await route.fetch();
    // Exact markup on purpose: the followers go at the end of the page's main.
    const body = (await response.text()).replace(
      "</main>",
      `${FOLLOWERS}</main>`,
    );
    await route.fulfill({ response, body });
  });
  await page.goto("/admin/items");
  const field = (name: string) => page.getByLabel(name, { exact: true });
  const button = page.getByRole("button", { name: /^Set to/ });
  const hint = page.getByText("Read the length");
  const was = page.getByText("Was 305 m");
  const total = page.getByText("After saving:");
  await field("Left").fill("40");
  await field("Package").selectOption("1");
  await field("Count").fill("300");
  await field("Moved").fill("7");
  await awayAndBack(page);
  await expect(field("Left")).toHaveValue("40");
  await expect(button).toHaveText("Set to 40 m");
  await expect(hint).toBeVisible();
  await expect(was).toBeVisible();
  await expect(total).toHaveText("After saving: 7 m");
});
