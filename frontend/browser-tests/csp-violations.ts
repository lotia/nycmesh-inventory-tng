/**
 * Every test fails on a Content-Security-Policy violation (backend
 * web/security_headers.py), in any page it opens: so every page the tests
 * reach, the crawl's all, is checked in each browser. The watch is a
 * script, so a test run without scripts is not watched. Tests import `test`
 * from here, or from own-rows.ts, which extends it, never Playwright's own
 * (biome.json).
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
 * securitypolicyviolation events say. A context a test opens itself is
 * watched with this too, and checked with `assertNone`.
 */
export async function watch(context: BrowserContext): Promise<string[]> {
  const found: string[] = [];
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
 * left is lost with it. Only a page's main frame is asked. Its pages must
 * run scripts: one that does not cannot be asked.
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
 * its refusals have settled. Without scripts nothing is watched, and a page
 * cannot be asked.
 */
export async function assertNone(
  context: BrowserContext,
  found: string[],
  javaScriptEnabled: boolean,
): Promise<void> {
  if (javaScriptEnabled) {
    await settled(context);
  }
  expect(found, "Content-Security-Policy violations").toEqual([]);
}

export const test = base.extend<{ violations: string[] }>({
  violations: [
    async ({ context, javaScriptEnabled }, use) => {
      const found = await watch(context);
      await use(found);
      await assertNone(context, found, javaScriptEnabled);
    },
    { auto: true },
  ],
});
