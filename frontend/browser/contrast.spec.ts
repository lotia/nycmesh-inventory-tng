/**
 * Text keeps WCAG 2.2 AA's 4.5:1 against what is behind it, in the light
 * scheme and the dark: a Retired list (inventory-tng-7sy, where fading a
 * whole section with opacity took its hints below it), and a link or
 * section with keyboard focus (docs/decisions/0019), which also shows it
 * plainly.
 */
import { expect, type Locator, test } from "@playwright/test";

/** An element's text against the nearest background behind it, and how
 * much anything around it fades it. */
function contrast(element: Locator) {
  return element.evaluate((at) => {
    /** WCAG 2.2's relative luminance of an rgb() colour. */
    const luminance = (colour: string) => {
      const [r, g, b] = (colour.match(/[\d.]+/g) ?? []).map(Number).map((c) => {
        const s = c / 255;
        return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
    };
    let faded = 1;
    let background: string | null = null;
    for (let up: Element | null = at; up; up = up.parentElement) {
      const style = getComputedStyle(up);
      faded *= Number(style.opacity);
      if (background === null && style.backgroundColor !== "rgba(0, 0, 0, 0)") {
        background = style.backgroundColor;
      }
    }
    const [light, dark] = [
      luminance(getComputedStyle(at).color),
      luminance(background ?? "rgb(255, 255, 255)"),
    ].sort((a, b) => b - a);
    return { faded, ratio: ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05) };
  });
}

for (const colorScheme of ["light", "dark"] as const) {
  test.describe(`in the ${colorScheme} scheme`, () => {
    test.use({ colorScheme });

    test("a Retired list's hints are readable", async ({ page }) => {
      await page.goto("/admin/volunteers");
      const hint = page.locator("#retired .hint").first();
      await expect(hint).toBeVisible();
      const { faded, ratio } = await contrast(hint);
      expect(faded).toBe(1);
      expect(ratio).toBeGreaterThanOrEqual(4.5);
    });

    test("a focused link is highlighted, and readable", async ({ page }) => {
      await page.goto("/admin/items");
      const link = page.getByRole("main").getByRole("link").first();
      await link.focus();
      // Focus from the keyboard, as :focus-visible needs.
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      await expect(link).toBeFocused();
      const style = await link.evaluate((at) => {
        const computed = getComputedStyle(at);
        return {
          background: computed.backgroundColor,
          thickness: computed.textDecorationThickness,
        };
      });
      expect(style.background).not.toBe("rgba(0, 0, 0, 0)");
      expect(style.thickness).toBe("3px");
      expect((await contrast(link)).ratio).toBeGreaterThanOrEqual(4.5);
    });

    test("a focused button has a thick outline", async ({ page }) => {
      await page.goto("/admin/items");
      const button = page.getByRole("main").getByRole("button").first();
      await button.focus();
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      await expect(button).toBeFocused();
      const outline = await button.evaluate((at) => {
        const computed = getComputedStyle(at);
        return [computed.outlineStyle, computed.outlineWidth];
      });
      expect(outline).toEqual(["solid", "3px"]);
    });
  });
}

test.describe("on a wide screen", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("the section this page is in is marked, without an underline", async ({
    page,
  }) => {
    await page.goto("/admin/items");
    const sections = page.getByRole("navigation", { name: "Sections" });
    const items = sections
      .filter({ visible: true })
      .getByRole("link", { name: "Items" });
    await expect(items).toHaveAttribute("aria-current", "page");
    const style = await items.evaluate((at) => {
      const computed = getComputedStyle(at);
      return {
        line: computed.textDecorationLine,
        height: at.getBoundingClientRect().height,
      };
    });
    expect(style.line).toBe("none");
    expect(style.height).toBeGreaterThanOrEqual(44);
  });
});
