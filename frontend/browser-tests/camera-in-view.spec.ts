/**
 * The scan page's camera stays where it is as labels are scanned, at a
 * phone's width, as each new line goes below it, and Back to camera brings
 * it back once the volunteer has scrolled away (Ali, 2026-11-24,
 * inventory-tng-fdnt). Chromium's camera here is a canvas this test draws
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

test("the camera stays put as labels are scanned, and Back to camera brings it back", async ({
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
  // Opened from the page's top, the picture runs off the screen's bottom:
  // Back to camera brings it wholly on, and goes.
  await back.click();
  await expect(picture).toBeInViewport({ ratio: 1 });
  await expect(back).toBeHidden();
  const before = await cameraPlace(page);

  for (const [at, name] of [radio, other, cable].entries()) {
    await show(page, labels[at] as Drawn);
    await expect(camera.getByRole("status")).toHaveText(`Added one: ${name}.`, {
      timeout: 15_000,
    });
    const now = await cameraPlace(page);
    expect(now.scrolled).toBe(before.scrolled);
    expect(Math.abs(now.top - before.top)).toBeLessThanOrEqual(2);
  }
  // Newest first: each line's item, named first in it.
  const lines = page
    .getByRole("heading", { name: "Cart" })
    .locator("xpath=following-sibling::ul[1]/li/div[1]/strong");
  await expect(lines).toHaveText([cable, other, radio]);

  // Down to the first scan's line, to type: no Back to camera meanwhile.
  const loose = looseBox(page, radio);
  await loose.fill("3");
  await expect(picture).not.toBeInViewport({ ratio: 1 });
  await expect(back).toBeHidden();
  await loose.blur();
  await expect(back).toBeVisible();

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
});
