/**
 * No page scrolls sideways on a phone (inventory-tng-8i4.60.5): every page
 * an administrator can reach by following links, and the volunteers' pages,
 * at 360 pixels wide.
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
 * This site's pages that a page leads to, path and query: its links, and
 * each choice of a form that only picks a page (a GET form with a list).
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
  const hrefs = [...links, ...choices];
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
 * Pages known to be wider than a phone, and the issue that fixes each: it
 * takes the page off this list.
 */
const KNOWN: Record<string, string> = {
  // Its sessions table: browsers' names are long.
  "/admin/account": "inventory-tng-8i4.60.3",
};

/** Fails if a page is wider than the screen, but for those KNOWN to be. */
function fits(widths: Map<string, number>): void {
  const wide = [...widths]
    .filter(([path, width]) => width > 0 && !(path in KNOWN))
    .map(([path, width]) => `${path}, by ${width} pixels`);
  expect(wide, "pages wider than the screen").toEqual([]);
}

test("every administrator page fits a phone", async ({ page }) => {
  await page.goto("/admin/sign-in");
  await page.getByLabel("Username").fill(process.env.ADMIN_USERNAME ?? "");
  await page.getByLabel("Password").fill(process.env.ADMIN_PASSWORD ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Signed in as")).toBeVisible();

  const widths = new Map<string, number>();
  const waiting = ["/admin"];
  for (let path = waiting.shift(); path !== undefined; path = waiting.shift()) {
    if (widths.has(path)) {
      continue;
    }
    const response = await page.goto(path);
    expect(response?.ok(), `${path} answers`).toBe(true);
    widths.set(path, await overflow(page));
    waiting.push(...(await linksOn(page)).filter((link) => !widths.has(link)));
  }
  // The crawl reached the pages behind the lists, not only the lists.
  expect([...widths.keys()].filter((path) => /\/\d+$/.test(path))).not.toEqual(
    [],
  );
  fits(widths);
});

test("signing in fits a phone", async ({ page }) => {
  await page.goto("/admin/sign-in");
  fits(new Map([["/admin/sign-in", await overflow(page)]]));
});

test("registering and the scan page fit a phone", async ({ page }) => {
  const widths = new Map<string, number>();
  await page.goto(`/register?code=${process.env.REGISTRATION_PHRASE ?? ""}`);
  widths.set("/register", await overflow(page));
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByText("This browser is registered.")).toBeVisible();
  await page.goto("/scan");
  await expect(page.getByRole("heading", { name: "Scan" })).toBeVisible();
  widths.set("/scan", await overflow(page));
  fits(widths);
});
