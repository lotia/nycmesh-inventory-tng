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
