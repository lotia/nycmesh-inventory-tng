/**
 * The scan page's camera stays where it is as labels are scanned, at a
 * phone's width, as each new line goes below it, and Back to camera brings
 * it back once the volunteer has scrolled away (Ali, 2026-11-24,
 * inventory-tng-fdnt). Opening the camera brings its picture wholly on the
 * screen, and off the screen it reads no labels (Ali, 2026-11-24,
 * inventory-tng-rj2g). The alert that a newer build is out, which the
 * first scan takes away, sits below it (Ali, 2026-11-24,
 * inventory-tng-oaao). Chromium's camera here is a canvas this test draws
 * each label on, in place of its fake camera's one label.
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { prepared } from "./prepared";
import { printLabels } from "./print-labels";
import { looseBox, startMove } from "./scan-page";

/** A label's QR code, as its print page draws it: the path of its dark
 * modules, and how many modules across, its border with them. */
type Drawn = { d: string; modules: number };

/** The label of the item named `name`, printed once from its page. */
async function labelOf(page: Page, name: string): Promise<Drawn> {
  await printLabels(page, 1, name);
  return page
    .locator(".print-label .qr svg")
    .first()
    .evaluate((svg) => {
      const path = svg.querySelector("path") as SVGPathElement;
      const scale = Number(
        /scale\(([\d.]+)\)/.exec(path.getAttribute("transform") ?? "")?.[1] ??
          1,
      );
      return {
        d: path.getAttribute("d") ?? "",
        modules: (svg as SVGSVGElement).viewBox.baseVal.width / scale,
      };
    });
}

/** The camera, a canvas showing whatever label the test draws on it. */
async function drawnCamera(page: Page) {
  await page.addInitScript(() => {
    const side = 480;
    const margin = 40;
    const canvas = document.createElement("canvas");
    canvas.width = side;
    canvas.height = side;
    const context = canvas.getContext("2d") as CanvasRenderingContext2D;
    let shown: Drawn | null = null;
    const draw = () => {
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.fillStyle = "#fff";
      context.fillRect(0, 0, side, side);
      if (shown !== null) {
        const scale = (side - 2 * margin) / shown.modules;
        context.setTransform(scale, 0, 0, scale, margin, margin);
        context.strokeStyle = "#000";
        context.lineWidth = 1;
        context.stroke(new Path2D(shown.d));
      }
    };
    // Drawn again and again, so the stream always has a frame.
    setInterval(draw, 100);
    Object.assign(window, {
      showLabel: (label: Drawn | null) => {
        shown = label;
        draw();
      },
    });
    navigator.mediaDevices.getUserMedia = async () => canvas.captureStream(10);
  });
}

/** Shows the camera `label`. */
async function show(page: Page, label: Drawn) {
  await page.evaluate((drawn) => {
    (window as unknown as { showLabel: (each: Drawn) => void }).showLabel(
      drawn,
    );
  }, label);
}

/** Where the camera's picture's top is on the screen, and how far the page
 * is scrolled. */
async function cameraPlace(page: Page) {
  return page.locator("video").evaluate((video) => ({
    top: video.getBoundingClientRect().top,
    scrolled: window.scrollY,
  }));
}

/** Waits for the page to stop scrolling: the same place two frames
 * running. */
async function settled(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((done) => {
        let last = Number.NaN;
        const check = () => {
          if (window.scrollY === last) {
            done();
          } else {
            last = window.scrollY;
            requestAnimationFrame(check);
          }
        };
        requestAnimationFrame(check);
      }),
  );
}

test("the camera stays put as labels are scanned, reads none off the screen, and Back to camera brings it back", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "only Chromium's camera is faked");
  const radio = prepared().labelled;
  const other = "NanoStation 5AC Loco";
  const cable = "Outdoor Cat6 cable";
  const labels = [
    await labelOf(page, radio),
    await labelOf(page, other),
    await labelOf(page, cable),
  ];
  await drawnCamera(page);
  await page.goto("/scan");
  // A move from the shelf, where the cable's box is open.
  await startMove(page);
  await page.getByRole("button", { name: "Scan with the camera" }).click();
  const camera = page.getByRole("region", { name: "Camera" });
  const picture = page.locator("video");
  const back = page.getByRole("button", { name: "Back to camera" });
  await expect(camera.getByRole("status")).toHaveText(
    "Point the camera at a label.",
    { timeout: 15_000 },
  );
  // Opened from the page's top, where the picture would run off the
  // screen's bottom: the page brings it wholly on, with no Back to camera.
  await expect(picture).toBeInViewport({ ratio: 1 });
  await expect(back).toBeHidden();
  // Another tab of a newer build: with the cart empty, the alert shows;
  // the first scan takes it away.
  const newer = await page.context().newPage();
  await newer.goto("/scan");
  await newer.evaluate(() =>
    localStorage.setItem(
      "inventory.build",
      JSON.stringify({ build: "2999-01-01T00:00:00.000Z" }),
    ),
  );
  // Its storage event is sent; closed, it can't hide this page.
  await newer.close();
  const alert = page.getByRole("alert").filter({ hasText: "updated" });
  await expect(alert).toBeVisible();
  await settled(page);
  const before = await cameraPlace(page);

  for (const [at, name] of [radio, other, cable].entries()) {
    await show(page, labels[at] as Drawn);
    await expect(camera.getByRole("status")).toHaveText(`Added one: ${name}.`, {
      timeout: 15_000,
    });
    const now = await cameraPlace(page);
    expect(now.scrolled).toBe(before.scrolled);
    expect(Math.abs(now.top - before.top)).toBeLessThanOrEqual(2);
    await expect(alert).toBeHidden();
  }
  // Newest first: each line's item, named first in it.
  const lines = page
    .getByRole("heading", { name: "Cart" })
    .locator("xpath=following-sibling::ul[1]/li/div[1]/strong");
  await expect(lines).toHaveText([cable, other, radio]);
  // Out of view longer than a repeat's quiet time before the picture
  // leaves the screen, the first label is a scan of its own when shown
  // again: one in view just before would count as held there throughout.
  await page.waitForTimeout(2_500);

  // Down to the first scan's line, to type: no Back to camera meanwhile.
  const loose = looseBox(page, radio);
  await loose.fill("3");
  await expect(picture).not.toBeInViewport({ ratio: 1 });
  await expect(back).toBeHidden();
  await loose.blur();
  await expect(back).toBeVisible();

  // Off the screen, a label shown is not read: its line is as it was
  // after longer than a repeat's quiet time (repeated-reads.ts), so it is
  // taken once read. The line says what it did after typing ended
  // (inventory-tng-0w3n).
  const status = camera.getByRole("status");
  await show(page, labels[0] as Drawn);
  await page.waitForTimeout(2_500);
  await expect(loose).toHaveValue("3");
  await expect(status).toHaveText("Point the camera at a label.");

  // At the page's bottom, nothing is left under it.
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  const button = await back.boundingBox();
  const discard = await page
    .getByRole("button", { name: "Discard cart" })
    .boundingBox();
  expect((discard?.y ?? 0) + (discard?.height ?? 0)).toBeLessThanOrEqual(
    button?.y ?? 0,
  );

  // By keyboard: focus goes to the camera, not lost as the button goes.
  await back.focus();
  await page.keyboard.press("Enter");
  await expect(picture).toBeInViewport({ ratio: 1 });
  await expect(back).toBeHidden();
  await expect(camera).toBeFocused();
  // Wholly on the screen again, the label is read.
  await expect(status).toHaveText(`Added one: ${radio}.`, { timeout: 15_000 });
  await expect(loose).toHaveValue("4");
});
