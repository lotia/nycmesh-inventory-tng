import { useSignal } from "@preact/signals";
import type { ComponentChildren, TargetedSubmitEvent } from "preact";
import { type Api, makeApi } from "./api/client";
import { Field } from "./Field";
import { type Outbox, sharedOutbox } from "./outbox";
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
export function RegisterPage({
  api = API,
  outbox = sharedOutbox(),
}: {
  api?: Api;
  outbox?: Outbox;
}) {
  const code = useSignal(
    new URLSearchParams(window.location.search).get(CODE) ?? "",
  );
  const sending = useSignal(false);
  const outcome = useSignal<Outcome | null>(null);
  // Batches saved here before registering, sent now (decision 0009).
  const waiting = useSignal(0);

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
      waiting.value = outbox.entries.value.filter(
        (entry) => entry.state === "waiting",
      ).length;
      void outbox.send();
    }
  }

  if (outcome.value?.kind === "registered") {
    return (
      <Page>
        <p role="status">This browser is registered.</p>
        {waiting.value > 0 && (
          <p>
            {waiting.value === 1
              ? "The batch waiting on this browser is being sent."
              : `The ${waiting.value} batches waiting on this browser are being sent.`}
          </p>
        )}
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
        Registering lets this browser record in the inventory. It covers only
        this browser, so use the same one for the scan page. If the QR code
        opened a browser you don't usually use, open the link in yours.
      </p>
      <form onSubmit={submit}>
        <Field label="Registration phrase">
          {(id) => (
            <input
              id={id}
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
          )}
        </Field>
        <button type="submit" class="primary" disabled={sending.value}>
          {sending.value ? "Registering…" : "Register"}
        </button>
      </form>
      {message !== null && <p role="alert">{message}</p>}
    </Page>
  );
}

function Page({ children }: { children: ComponentChildren }) {
  return (
    <main class="volunteer page-width">
      <h1>Register this device</h1>
      {children}
    </main>
  );
}
