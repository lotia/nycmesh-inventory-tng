/**
 * Every test fails on a Content-Security-Policy violation (backend
 * web/security_headers.py), in any page it opens: so every page the tests
 * reach, the crawl's all, is checked in each browser, with scripts or
 * without. Tests import `test` from here, or from own-rows.ts, which
 * extends it, never Playwright's own (biome.json).
 */
import {
  type BrowserContext,
  test as base,
  expect,
  type Page,
} from "@playwright/test";

/** What a page's securitypolicyviolation event says. */
type Violation = { directive: string; blocked: string; target: string };

/** What the watch adds to each page's window. */
type Watched = {
  /** Gives a refusal to the test, once it has it. */
  policyViolated(violation: Violation): Promise<void>;
  /** Once the refusals the page has made so far have reached the test. */
  policySettled(): Promise<void>;
};

/**
 * Collects what the policy refuses in `context`'s pages, as each one's
 * securitypolicyviolation events say; in a context that runs no scripts,
 * where no listener can be added, as the browser's console does. Not its
 * reports: Chromium sends those by the Reporting API, over HTTPS only. A
 * context a test opens itself is watched with this too, and checked with
 * `assertNone`.
 */
export async function watch(
  context: BrowserContext,
  javaScriptEnabled = true,
): Promise<string[]> {
  const found: string[] = [];
  if (!javaScriptEnabled) {
    context.on("console", (message) => {
      // Each browser's own words, which both say this.
      if (/Content Security Policy/.test(message.text())) {
        found.push(`${message.text()} on ${message.page()?.url()}`);
      }
    });
    return found;
  }
  await context.exposeBinding(
    "policyViolated",
    ({ page }, violation: Violation) => {
      const { directive, blocked, target } = violation;
      found.push(
        `${directive} refused ${blocked} (${target}) on ${page.url()}`,
      );
    },
  );
  await context.addInitScript(() => {
    const watched = window as unknown as Watched;
    const given: Promise<void>[] = [];
    document.addEventListener(
      "securitypolicyviolation",
      (event) => {
        const target = event.target as Node | null;
        given.push(
          watched.policyViolated({
            directive: event.effectiveDirective,
            blocked: event.blockedURI,
            target: target?.nodeName ?? "",
          }),
        );
      },
      true,
    );
    watched.policySettled = async () => {
      // The browser fires the event a task after it refuses.
      await new Promise((resolve) => setTimeout(resolve));
      await Promise.all(given);
    };
  });
  return found;
}

/**
 * Until the refusals made in `context`'s pages so far, by a test's last
 * action too, have reached the test. A page that is navigating is waited
 * for, and the document it loads asked; one that closed has nothing more
 * to give. A refusal the old document had not yet reported when it was
 * left is lost with it. Only a page's main frame is asked. A page that
 * runs no scripts is asked too, by Playwright's own: its console's
 * messages come before the answer.
 */
export async function settled(context: BrowserContext): Promise<void> {
  await Promise.all(context.pages().map(pageSettled));
}

async function pageSettled(page: Page): Promise<void> {
  for (;;) {
    try {
      await page.waitForLoadState();
      await page.evaluate(() =>
        (window as unknown as Partial<Watched>).policySettled?.(),
      );
      return;
    } catch (error) {
      if (page.isClosed()) return;
      // A navigation replaced the document while it was asked.
      if (!String(error).includes("Execution context was destroyed")) {
        throw error;
      }
    }
  }
}

/**
 * Fails after the test if a page of `context` violated the policy, once
 * its refusals have settled.
 */
export async function assertNone(
  context: BrowserContext,
  found: string[],
): Promise<void> {
  await settled(context);
  expect(found, "Content-Security-Policy violations").toEqual([]);
}

export const test = base.extend<{ violations: string[] }>({
  violations: [
    async ({ context, javaScriptEnabled }, use) => {
      const found = await watch(context, javaScriptEnabled);
      await use(found);
      await assertNone(context, found);
    },
    { auto: true },
  ],
});
