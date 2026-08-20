import { test as base, expect } from "@playwright/test";
import { FAKE_CAMERA } from "./camera";
import { filmed, mintLabel } from "./qrVideo";
import { SIGN_IN_MAY_WAIT_FOR_A_FRESH_CODE, seeded, signIn } from "./sign-in";

/**
 * The one thing no other test in this repository can say: a printed label,
 * seen through a camera, becomes a line in the batch.
 *
 * Everything either side of the decode is already covered elsewhere. The
 * stream lifecycle and the loop's rules are asserted without a browser in
 * `src/scan/CameraScanner.test.tsx` and `src/scan/decodeLoop.test.ts`, and
 * that the printed sticker itself is readable is asserted in the backend
 * suite, which decodes what the label generator drew with a different
 * implementation from the one that drew it. What none of them can reach is the
 * handoff in the middle: `scan/frame.ts` draws the video into a canvas and
 * hands the decoder an `ImageData`, and if that were wrong in any of the ways
 * it can be -- a detached buffer, a tainted canvas, the wrong pixel format, a
 * downscale that eats the modules -- every one of those suites would stay
 * green while a volunteer at a shelf scanned nothing at all.
 *
 * So this drives the real pipeline: a real `getUserMedia` stream carrying a
 * real symbol at the project's own error correction level, a real `play()`, a
 * real frame grab, a real WebAssembly decode, and the cart line that comes out
 * the far side. How the clip is made is in qrVideo.ts.
 *
 * The first time it ran, it failed. The camera decoded the label's own deep
 * link -- which is what the symbol carries, by decision 0011 section 3 -- and
 * the app sent the whole URL to the resolver, so the in-app scanner had never
 * once worked against this project's own stickers while every other suite was
 * green. That is the bug this file exists to have caught, and `codeFromScan`
 * in src/scan/deepLink.ts is the fix.
 */

/** What the sticker says is in the box. Not 1, so the quantity is falsifiable. */
const QUANTITY = 5;

/** The decoder is a megabyte and the loop runs at 5 Hz, so this is not instant. */
const DECODE_MAY_TAKE = 30_000;

/**
 * The clip, and the browser told to use it as its camera.
 *
 * Fixtures rather than steps inside the test, because the flag naming the file
 * is a *launch* argument: the clip has to exist before Chromium starts.
 * Overriding `launchOptions` is how Playwright is asked for that, and it keeps
 * everything else the project already configured -- a trace retained on
 * failure most of all, which the one test here that is expensive to debug
 * should not be the only one to go without.
 */
const test = base.extend<object, { clip: string }>({
  clip: [
    // biome-ignore lint/correctness/noEmptyPattern: Playwright reads a fixture's dependencies off this pattern and refuses a first parameter that is not one
    async ({}, use) => {
      const clip = filmed(mintLabel(QUANTITY, Number(seeded("item"))));
      await use(clip.path);
      clip.remove();
    },
    { scope: "worker" },
  ],
  launchOptions: [
    async ({ clip }, use) => {
      await use({
        args: [
          ...FAKE_CAMERA,
          // The one flag that separates this from decoder-origin.spec.ts: the
          // fake device reads its frames from this file instead of generating
          // a pattern, and loops it for as long as the camera is open.
          `--use-file-for-fake-video-capture=${clip}`,
        ],
      });
    },
    { scope: "worker" },
  ],
});

test.use({ permissions: ["camera"] });

// The suite's own timeout is sized for a sign-in that may wait out a spent
// one-time password, and this test signs in like every other. Waiting for a
// decode on top of that does not fit inside it, and what would be reported is
// a bare timeout rather than whatever was actually slow.
test.describe.configure({ timeout: SIGN_IN_MAY_WAIT_FOR_A_FRESH_CODE + DECODE_MAY_TAKE });

test("a printed label, filmed and scanned, becomes a line in the batch", async ({ page }) => {
  // Signed in because resolving a scanned code reads the API, which asks for a
  // session today. Nobody is picked as the volunteer: that decides who the
  // batch is attributed to when it is saved, and this stops at the line.
  await signIn(page);
  await page.getByRole("button", { name: "Camera", exact: true }).click();

  // Asserted before the decode, as decoder-origin.spec.ts asserts it: a camera
  // that never opened decodes nothing, and a test waiting for a cart line it
  // could never get would spend the whole timeout saying so.
  await expect(page.getByLabel("Camera preview")).toBeVisible();

  const line = page.getByRole("list", { name: "This batch" }).getByRole("listitem");
  await expect(line).toContainText(seeded("item_name"), { timeout: DECODE_MAY_TAKE });

  // The amount is read rather than matched, because the sticker never leaves
  // the frame: SCAN_DEBOUNCE_MS in cart/cartState.ts lets the same label be
  // scanned again three quarters of a second later, so by the time this is
  // read the line may be worth several stickers. What holds however many
  // landed is that every one of them was worth what the label says -- which a
  // quantity defaulted to 1, or read off the item instead of the label, does
  // not satisfy.
  const amount = Number(/\d+/.exec((await line.textContent()) ?? "")?.[0]);
  expect(amount).toBeGreaterThan(0);
  expect(amount % QUANTITY).toBe(0);
});
