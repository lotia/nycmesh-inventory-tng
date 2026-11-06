/**
 * Scanning a label with the camera (inventory-tng-8i4.13.7), in Chromium,
 * whose fake camera shows the seeded radio's label (playwright.config.ts):
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
  await page.goto(`/register?code=${process.env.REGISTRATION_PHRASE ?? ""}`);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByText("This browser is registered.")).toBeVisible();
  await page.goto("/scan");
  await page.getByRole("button", { name: "Scan with the camera" }).click();
  await expect(
    page.getByText("Added one: LiteBeam 5AC Gen2.", { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Nothing in the cart yet.")).toBeHidden();
  await page.getByRole("button", { name: "Done" }).click();
  expect(elsewhere).toEqual([]);
});
