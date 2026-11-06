/**
 * Scanning a label with the camera (inventory-tng-8i4.13.7), in Chromium,
 * whose fake camera shows the seed's labelled item (playwright.config.ts):
 * the camera, the decoder from this origin and the cart, together.
 */
import { expect, test } from "@playwright/test";

test("a label shown to the camera adds its item to the cart", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "only Chromium has a fake camera");
  // The decoder's WebAssembly comes from this site, never another.
  const site = new URL(process.env.BROWSER_BASE_URL ?? "").origin;
  const elsewhere: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== site) {
      elsewhere.push(request.url());
    }
  });
  await page.goto("/scan");
  await page.getByRole("button", { name: "Scan with the camera" }).click();
  await expect(
    page.getByText(`Added one: ${process.env.LABELLED_ITEM}.`, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Nothing in the cart yet.")).toBeHidden();
  await page.getByRole("button", { name: "Done" }).click();
  expect(elsewhere).toEqual([]);
});
