/**
 * The Content-Security-Policy (backend security_headers.py,
 * inventory-tng-e0y): what it refuses fails a test, and what the pages
 * need, it allows.
 */
import { expect } from "@playwright/test";
import { test } from "./policy";

test("a violation is caught, so it would fail the test", async ({
  page,
  violations,
}) => {
  await page.goto("/scan");
  await page.evaluate(() => {
    const inline = document.createElement("script");
    inline.textContent = "document.title = 'injected';";
    document.body.append(inline);
  });
  await expect.poll(() => violations.join("\n")).toContain("script-src");
  await expect(page).not.toHaveTitle("injected");
  // Caught: this test's own, made on purpose.
  violations.length = 0;
});

test("the scan page may compile the decoder's WebAssembly", async ({
  page,
}) => {
  await page.goto("/scan");
  // The smallest module there is: its header alone.
  const compiled = await page.evaluate(async () => {
    const module = await WebAssembly.compile(
      new Uint8Array([0, 0x61, 0x73, 0x6d, 1, 0, 0, 0]),
    );
    return module instanceof WebAssembly.Module;
  });
  expect(compiled).toBe(true);
});

test("a <select> is refused nothing, and keeps its ellipsis", async ({
  page,
  browserName,
  violations,
}) => {
  const reports: string[] = [];
  await page.route("**/api/csp-reports", async (route) => {
    reports.push(route.request().postData() ?? "");
    await route.fulfill({ status: 204 });
  });
  // The scan page has selects of its own too.
  await page.goto("/scan");
  // What the policy refuses, by id, until a style attribute of ours is:
  // Safari 27.2 refuses its own style for a <select> as each is made
  // (WebKit bug 327212, fixed in Playwright 1.65's WebKit), unless the
  // policy allows it by its hash (security_headers.py). A page's own
  // attribute of the same text is allowed with it.
  const refused = await page.evaluate(
    () =>
      new Promise<string[]>((resolve) => {
        const seen: string[] = [];
        document.addEventListener(
          "securitypolicyviolation",
          (event) => {
            const { id } = event.target as Element;
            seen.push(id);
            if (id === "ours") resolve(seen);
          },
          true,
        );
        // Two selects, cut short by an ellipsis or not, set by script,
        // which the policy allows.
        for (const textOverflow of ["ellipsis", "clip"]) {
          const select = document.createElement("select");
          select.id = textOverflow;
          select.style.width = "6em";
          select.style.textOverflow = textOverflow;
          select.append(new Option("An option too long for its select"));
          document.body.prepend(select);
        }
        const same = document.createElement("p");
        same.id = "same";
        same.setAttribute("style", "text-overflow:inherit");
        const ours = document.createElement("p");
        ours.id = "ours";
        ours.setAttribute("style", "color: red");
        document.body.append(same, ours);
      }),
  );
  expect(refused).toEqual(["ours"]);
  // WebKit reports ours alone; Chromium sends its reports by the Reporting
  // API, over HTTPS only.
  await expect
    .poll(() => reports.length)
    .toBe(browserName === "webkit" ? 1 : 0);
  // Caught: this test's own, made on purpose, and nothing else.
  await expect
    .poll(() => violations)
    .toEqual([expect.stringMatching(/^style-src-attr refused inline \(P\) /)]);
  violations.length = 0;
  // Refused, WebKit's style takes the select's text-overflow with it.
  // Not hiding the caret, which Playwright does by a <style> of its own.
  const drawn = (id: string) =>
    page.locator(`#${id}`).screenshot({ caret: "initial" });
  expect((await drawn("ellipsis")).equals(await drawn("clip"))).toBe(false);
  // WebKit refuses a <style> Playwright adds to take a screenshot anyway.
  violations.length = 0;
});
