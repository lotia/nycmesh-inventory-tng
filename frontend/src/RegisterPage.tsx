import { useSignal } from "@preact/signals";
import type { ComponentChildren, TargetedSubmitEvent } from "preact";
import { type Api, makeApi } from "./api/client";
import { type Outcome, REFUSALS, register } from "./registration";

const CODE = "code";

// One for every page, rather than one for each render.
const API = makeApi();

const PROBLEMS = {
  blank: "Enter the phrase you were given.",
  unavailable: "The inventory can't answer just now. Try again in a minute.",
  failed:
    "Couldn't reach the inventory. Check your connection, then try again.",
};

function problem(outcome: Outcome | null): string | null {
  if (outcome === null || outcome.kind === "registered") {
    return null;
  }
  return outcome.kind === "refused"
    ? REFUSALS[outcome.reason]
    : PROBLEMS[outcome.kind];
}

/** The page a registration QR code opens: /register?code=<phrase>. */
export function RegisterPage({ api = API }: { api?: Api }) {
  const code = useSignal(
    new URLSearchParams(window.location.search).get(CODE) ?? "",
  );
  const sending = useSignal(false);
  const outcome = useSignal<Outcome | null>(null);

  async function submit(event: TargetedSubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    // One attempt at a time: a single-use phrase must not be sent twice.
    if (sending.value) {
      return;
    }
    sending.value = true;
    // Cleared, so the same refusal again is announced again.
    outcome.value = null;
    outcome.value = await register(api, code.value);
    sending.value = false;
    if (outcome.value.kind === "registered") {
      // The phrase has done its job: keep it out of the address bar and
      // the browser's history.
      const url = new URL(window.location.href);
      url.searchParams.delete(CODE);
      window.history.replaceState(null, "", url);
    }
  }

  if (outcome.value?.kind === "registered") {
    return (
      <Page>
        <p role="status">This browser is registered.</p>
        <p>
          <a href="/scan">Go to the scan page</a>
        </p>
      </Page>
    );
  }

  const message = problem(outcome.value);
  return (
    <Page>
      <p>
        Registering lets this browser save to the inventory. It covers only this
        browser, so use the same one for the scan page. If the QR code opened a
        browser you don't usually use, open the link in yours.
      </p>
      <form onSubmit={submit}>
        <label for="code">Registration phrase</label>
        <input
          id="code"
          name="code"
          required
          autoComplete="off"
          autoCapitalize="none"
          spellcheck={false}
          value={code.value}
          onInput={(event) => {
            code.value = event.currentTarget.value;
          }}
        />
        <button type="submit" disabled={sending.value}>
          {sending.value ? "Registering…" : "Register"}
        </button>
      </form>
      {message !== null && <p role="alert">{message}</p>}
    </Page>
  );
}

function Page({ children }: { children: ComponentChildren }) {
  return (
    <main>
      <h1>Register this device</h1>
      {children}
    </main>
  );
}
