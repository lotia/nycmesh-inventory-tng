/**
 * Every test fails on a Content-Security-Policy violation (backend
 * security_headers.py), in any page it opens: so every page the tests
 * reach, the crawl's all, is checked in each browser. Tests import `test`
 * from here, or from own.ts, which extends it, never Playwright's own
 * (biome.json).
 */
import { type BrowserContext, test as base, expect } from "@playwright/test";

/** What a page's securitypolicyviolation event says. */
type Violation = { directive: string; blocked: string; target: string };

/**
 * WebKit refuses its own inline style for every <select>, saying so as a
 * violation of style-src-attr by the select: not the page's. A style
 * attribute the page gives a select is still caught in Chromium, and
 * refused in any template by a backend test.
 */
function webkitsOwn(violation: Violation, browserName: string): boolean {
  return (
    browserName === "webkit" &&
    violation.directive === "style-src-attr" &&
    violation.target === "SELECT"
  );
}

/**
 * Collects what the policy refuses in `context`'s pages, as each one's
 * securitypolicyviolation events say. A context a test opens itself is
 * watched with this too.
 */
export async function watch(context: BrowserContext): Promise<string[]> {
  const found: string[] = [];
  const browserName = context.browser()?.browserType().name() ?? "";
  await context.exposeBinding(
    "policyViolated",
    ({ page }, violation: Violation) => {
      if (!webkitsOwn(violation, browserName)) {
        const { directive, blocked, target } = violation;
        found.push(
          `${directive} refused ${blocked} (${target}) on ${page.url()}`,
        );
      }
    },
  );
  await context.addInitScript(() => {
    document.addEventListener(
      "securitypolicyviolation",
      (event) => {
        const target = event.target as Node | null;
        (
          window as unknown as { policyViolated(violation: Violation): void }
        ).policyViolated({
          directive: event.effectiveDirective,
          blocked: event.blockedURI,
          target: target?.nodeName ?? "",
        });
      },
      true,
    );
  });
  return found;
}

/** Fails after the test if a page of its context violated the policy. */
export function assertNone(found: string[]): void {
  expect(found, "Content-Security-Policy violations").toEqual([]);
}

export const test = base.extend<{ violations: string[] }>({
  violations: [
    async ({ context }, use) => {
      const found = await watch(context);
      await use(found);
      assertNone(found);
    },
    { auto: true },
  ],
});
