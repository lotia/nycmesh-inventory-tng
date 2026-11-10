/**
 * Text in a Retired list keeps WCAG 2.2 AA's 4.5:1 (inventory-tng-7sy):
 * fading a whole section (opacity) took its hints below it.
 */
import { expect, test } from "@playwright/test";

test("a Retired list's hints are readable", async ({ page }) => {
  await page.goto("/admin/volunteers");
  const hint = page.locator(".retired .hint").first();
  await expect(hint).toBeVisible();
  const ratio = await hint.evaluate((element) => {
    /** WCAG 2.2's relative luminance of an rgb() colour. */
    const luminance = (colour: string) => {
      const [r, g, b] = (colour.match(/[\d.]+/g) ?? []).map(Number).map((c) => {
        const s = c / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
    };
    // Nothing between the hint and the page may fade it.
    let faded = 1;
    let background = "rgb(255, 255, 255)";
    for (let at: Element | null = element; at; at = at.parentElement) {
      const style = getComputedStyle(at);
      faded *= Number(style.opacity);
      if (
        background === "rgb(255, 255, 255)" &&
        style.backgroundColor !== "rgba(0, 0, 0, 0)"
      ) {
        background = style.backgroundColor;
      }
    }
    const [light, dark] = [
      luminance(getComputedStyle(element).color),
      luminance(background),
    ].sort((a, b) => b - a);
    return faded < 1 ? 0 : ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(4.5);
});
