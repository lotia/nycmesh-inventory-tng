/**
 * No page scrolls sideways on a phone (inventory-tng-8i4.60.5): every page
 * an administrator can reach by following links, and the volunteers' pages,
 * at 360 pixels wide, also with a long word in everything that shows text.
 */
import { expect, type Page, test } from "@playwright/test";

/** How far past the screen's edge the page reaches, in pixels. */
async function overflow(page: Page): Promise<number> {
  return page.evaluate(
    () =>
      document.documentElement.scrollWidth -
      document.documentElement.clientWidth,
  );
}

/**
 * This site's pages that a page leads to, path and query: its links, each
 * choice of a form that only picks a page (a GET form with a list), and
 * each page a lone button opens (a GET form with nothing to fill in, such
 * as one asking to confirm).
 */
async function linksOn(page: Page): Promise<string[]> {
  const links = await page
    .locator("a[href]")
    .evaluateAll((found) =>
      found.map((link) => (link as HTMLAnchorElement).href),
    );
  const choices = await page
    .locator('form[method="get"] select[name]')
    .evaluateAll((found) =>
      found.flatMap((select) => {
        const form = (select as HTMLSelectElement).form;
        if (form === null) {
          return [];
        }
        return [...(select as HTMLSelectElement).options]
          .filter((option) => option.value !== "")
          .map((option) => {
            const url = new URL(form.action);
            url.searchParams.set(
              (select as HTMLSelectElement).name,
              option.value,
            );
            return url.href;
          });
      }),
    );
  const buttons = await page
    .locator('form[method="get"]:has(button):not(:has(input, select))')
    .evaluateAll((found) =>
      found.map((form) => (form as HTMLFormElement).action),
    );
  const hrefs = [...links, ...choices, ...buttons];
  const here = new URL(page.url()).origin;
  return hrefs
    .map((href) => new URL(href))
    .filter(
      (url) =>
        url.origin === here &&
        !url.pathname.startsWith("/static/") &&
        !url.pathname.startsWith("/api/"),
    )
    .map((url) => {
      // What a page says after a change is not another page.
      url.searchParams.delete("notice");
      return url.pathname + url.search;
    });
}

/**
 * Whatever the data: a long word, such as a pasted link or a name typed
 * without spaces, in every element that shows text.
 */
async function longWords(page: Page): Promise<void> {
  await page.evaluate(() => {
    const showing = new Set<Element>();
    const texts = document.createTreeWalker(
      document.body,
      NodeFilter.SHOW_TEXT,
    );
    for (let text = texts.nextNode(); text; text = texts.nextNode()) {
      const parent = text.parentElement;
      if (
        text.textContent?.trim() &&
        parent &&
        !parent.closest("script, style")
      ) {
        showing.add(parent);
      }
    }
    for (const element of showing) {
      element.append(` ${"x".repeat(80)}`);
    }
  });
}

/** Fails if a page is wider than the screen. */
function fits(widths: Map<string, number>): void {
  const wide = [...widths]
    .filter(([, width]) => width > 0)
    .map(([path, width]) => `${path}, by ${width} pixels`);
  expect(wide, "pages wider than the screen").toEqual([]);
}

test("every administrator page fits a phone", async ({ page }) => {
  const widths = new Map<string, number>();
  const waiting = ["/admin"];
  for (let path = waiting.shift(); path !== undefined; path = waiting.shift()) {
    if (widths.has(path)) {
      continue;
    }
    const response = await page.goto(path);
    expect(response?.ok(), `${path} answers ${response?.status()}`).toBe(true);
    const links = await linksOn(page);
    await longWords(page);
    widths.set(path, await overflow(page));
    waiting.push(...links.filter((link) => !widths.has(link)));
  }
  // The crawl reached the pages behind the lists, not only the lists.
  expect([...widths.keys()].filter((path) => /\/\d+$/.test(path))).not.toEqual(
    [],
  );
  // And the pages asking to confirm what can't be undone.
  for (const asks of ["/revoke", "/reset", "/merge?"]) {
    expect(
      [...widths.keys()].some((path) => path.includes(asks)),
      asks,
    ).toBe(true);
  }
  fits(widths);
});

test.describe("signed out", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test("signing in fits a phone", async ({ page }) => {
    await page.goto("/admin/sign-in");
    fits(new Map([["/admin/sign-in", await overflow(page)]]));
  });
});

test("registering and the scan page fit a phone", async ({ page }) => {
  const widths = new Map<string, number>();
  for (const path of ["/register", "/scan"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await longWords(page);
    widths.set(path, await overflow(page));
  }
  fits(widths);
});
