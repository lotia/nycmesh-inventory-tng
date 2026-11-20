/**
 * Scanning a label with the camera (inventory-tng-8i4.13.7), in Chromium,
 * whose fake camera shows the seed's labelled item (playwright.config.ts):
 * the camera, the decoder from this origin and the cart, together, and the
 * session reported as the camera closes.
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { prepared } from "./prepared";

test("a label shown to the camera adds its item to the cart", async ({
  page,
  browserName,
  baseURL,
}) => {
  test.skip(browserName !== "chromium", "only Chromium has a fake camera");
  // The decoder's WebAssembly comes from this site, never another.
  const site = new URL(baseURL ?? "").origin;
  const elsewhere: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== site) {
      elsewhere.push(request.url());
    }
  });
  await keepBeacons(page);
  await page.goto("/scan");
  await page.getByRole("button", { name: "Scan with the camera" }).click();
  await expect(
    page.getByText(`Added one: ${prepared().labelled}.`, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Nothing in the cart yet.")).toBeHidden();
  await page.getByRole("button", { name: "Done" }).click();
  // The camera's report, sent as it closes.
  await expect
    .poll(async () => (await beaconed(page)).map((event) => event.kind))
    .toContain("camera_session");
  expect(
    (await beaconed(page)).filter((event) => event.kind === "camera_session"),
  ).toEqual([
    expect.objectContaining({
      outcome: "opened",
      // Chromium reads with its own decoder only where the system has one.
      decoder: expect.stringMatching(/^(native|ponyfill)$/),
      labels: expect.objectContaining({ package: 0, place: 0, unknown: 0 }),
    }),
  ]);
  expect(elsewhere).toEqual([]);
});

/** Keeps what the page sends by sendBeacon, which Playwright shows with no
 * body, as well as sending it. */
async function keepBeacons(page: Page) {
  await page.addInitScript(() => {
    const kept: string[] = [];
    const send = navigator.sendBeacon.bind(navigator);
    Object.assign(window, { kept });
    navigator.sendBeacon = (url, body) => {
      void (body as Blob).text().then((text) => kept.push(text));
      return send(url, body);
    };
  });
}

/** The events the page sent by sendBeacon (keepBeacons). */
async function beaconed(page: Page): Promise<{ kind: string }[]> {
  const kept = await page.evaluate(
    () => (window as unknown as { kept: string[] }).kept,
  );
  return kept.flatMap((text) => JSON.parse(text).events);
}
