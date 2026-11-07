/**
 * A form that makes something shown once is sent once, however often its
 * button is clicked (inventory-tng-8i4.83): a second sending would answer
 * "Already sent", and the code the first made would never be seen.
 */
import { expect, type Locator, test } from "@playwright/test";
import { makeCode } from "./codes";

/**
 * Clicked twice, a person's double-click apart: Playwright's dblclick fires
 * both in one task, and Chromium sends such a pair once by itself.
 */
async function doubleClick(button: Locator): Promise<void> {
  await button.evaluate((element: HTMLButtonElement) => {
    element.click();
    setTimeout(() => element.click(), 30);
  });
}

test("a double-click sends the form once, and shows the code", async ({
  page,
}) => {
  let posts = 0;
  page.on("request", (request) => {
    if (request.method() === "POST") posts += 1;
  });
  // Asked to confirm, the confirm form is sent next: once too.
  const asked = await makeCode(page, "Double-click", doubleClick);
  expect(posts).toBe(asked ? 2 : 1);
});
