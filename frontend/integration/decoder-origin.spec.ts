import { expect, test } from "@playwright/test";
import { FAKE_CAMERA } from "./camera";

/**
 * Where the label decoder's WebAssembly actually comes from.
 *
 * "Self-host the `.wasm` binary" is the first of the three non-optional
 * constraints in docs/decisions/0011-qr-batch-scanning.md section 2: stock
 * lives in a basement, the frontend image is meant to be self-contained, and
 * `barcode-detector` fetches its binary from a public CDN unless it is told
 * otherwise.
 *
 * Only a browser can answer this. The unit suite can assert what
 * `locateFile()` returns, and does (src/scan/decoder.test.ts), but not that the
 * ponyfill asked it -- and looking for the CDN's hostname in the built
 * JavaScript proves nothing either way, because `zxing-wasm`'s default is still
 * in the ponyfill chunk as a string nothing reaches. What distinguishes a
 * working self-host from a broken one is the request that gets made, so that
 * is what this opens a camera and watches.
 *
 * Chromium's fake camera is what makes it possible headlessly: a synthetic
 * stream and an auto-accepted permission prompt, so `getUserMedia` resolves and
 * the decoder is fetched exactly as it is for a volunteer. Nothing is decoded
 * from it and nothing here pretends otherwise.
 *
 * What this catches, checked by breaking it: dropping the
 * `prepareZXingModule` call fails it with
 * `https://fastly.jsdelivr.net/npm/zxing-wasm@.../zxing_reader.wasm` in the
 * list, which is `zxing-wasm`'s own default and the regression that matters.
 * What it cannot catch is narrower and worth writing down: this suite runs the
 * Vite dev server (see DEVELOPERS.md "Integration tests"), which serves the
 * ponyfill chunk from this origin, so an override replaced by an empty object
 * -- no `locateFile` at all -- leaves Emscripten resolving against that chunk's
 * own URL and still lands here. In the built image that path resolves to an
 * unhashed filename nothing serves, which is why the status is asserted below
 * as well as the origin.
 */

/** The public CDNs a mis-wired ponyfill would reach for. */
const CDN = /jsdelivr|unpkg|cdnjs|jspm|esm\.sh/i;

test.use({
  launchOptions: { args: FAKE_CAMERA },
  permissions: ["camera"],
});

test("the decoder's WebAssembly is fetched from the origin serving the app", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL as string).origin;
  const wasm: string[] = [];
  const elsewhere: string[] = [];
  // Collected as they happen rather than waited for afterwards: the fetch is
  // started by the decoder's own constructor, so a `waitForRequest` registered
  // after the camera opened is a race with it that would spend the whole test
  // timeout losing.
  const served: { url: string; status: number }[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (new URL(url).pathname.endsWith(".wasm")) {
      wasm.push(url);
    }
    if (!url.startsWith(`${origin}/`)) {
      elsewhere.push(url);
    }
  });
  page.on("response", (response) => {
    if (new URL(response.url()).pathname.endsWith(".wasm")) {
      served.push({ url: response.url(), status: response.status() });
    }
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Camera", exact: true }).click();
  // Asserted before anything else: a camera that did not open asks for no
  // decoder, and a test that watched for a request nobody made would pass
  // while proving nothing at all.
  await expect(page.getByLabel("Camera preview")).toBeVisible();

  await expect
    .poll(() => wasm.length, {
      message: "no .wasm was requested at all",
      timeout: 30_000,
    })
    .toBeGreaterThan(0);

  expect(wasm.filter((url) => !url.startsWith(`${origin}/`))).toEqual([]);
  // Served, not merely addressed to us: a same-origin URL that 404s is a
  // scanner that does not work, and would otherwise read as a pass.
  await expect.poll(() => served.length, { timeout: 30_000 }).toBeGreaterThan(0);
  expect(served.filter(({ status }) => status !== 200)).toEqual([]);
  // Separately from the check above, and deliberately about every request the
  // page made rather than only the binary: a CDN reached for anything is the
  // dependency this constraint exists to refuse.
  expect(elsewhere.filter((url) => CDN.test(url))).toEqual([]);
});
