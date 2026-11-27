import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { useSignal } from "@preact/signals";
import type { ComponentChildren, TargetedSubmitEvent } from "preact";
import { useEffect, useId, useRef } from "preact/hooks";
import { type Api, makeApi } from "../api/client";
import { Field } from "../shared/Field";
import { say } from "../shared/i18n";
import { type Outbox, sharedOutbox } from "../shared/outbox";
import { type Outcome, REFUSALS, register } from "../shared/registration";
import { focusInPlace, ignoreTaps } from "../shared/settling";

const CODE = "code";

export const REGISTERED = msg`This browser is registered.`;
export const BATCHES_WAITING = msg({
  message:
    "{count, plural, one {The batch waiting on this browser is being sent.} other {The # batches waiting on this browser are being sent.}}",
});

// One for every page, rather than one for each render.
const API = makeApi();

export const GO_TO_SCAN = msg`Go to the scan page`;
export const WHAT_REGISTERING_DOES = msg`Registering lets this browser record in the inventory. It covers only this browser, so use the same one for the scan page. If the QR code opened a browser you don't usually use, open the link in yours.`;
export const PHRASE = msg`Registration phrase`;
export const REGISTERING = msg`Registering…`;
export const REGISTER = msg`Register`;
export const REGISTER_THIS_DEVICE = msg`Register this device`;

export const PROBLEMS = {
  blank: msg`Enter the phrase you were given.`,
  unavailable: msg`The inventory can't answer just now. Try again in a minute.`,
  failed: msg`Couldn't reach the inventory. Check your connection, then try again.`,
};

function problem(outcome: Outcome | null): MessageDescriptor | null {
  if (outcome === null || outcome.kind === "registered") {
    return null;
  }
  return outcome.kind === "refused"
    ? REFUSALS[outcome.reason]
    : PROBLEMS[outcome.kind];
}

/** Whether `outcome` refuses the phrase as typed, so its box is marked
 * invalid (inventory-tng-gjuq): not too many tries, nor no answer. */
function phraseRefused(outcome: Outcome | null): boolean {
  return (
    outcome?.kind === "blank" ||
    (outcome?.kind === "refused" && outcome.reason !== "rate_limited")
  );
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
  const problemId = useId();
  const registered = useRef<HTMLParagraphElement>(null);
  const done = outcome.value?.kind === "registered";
  // The form gives way to what registering did, and Go to the scan page
  // may come under the finger: taps wait a moment, and focus goes to the
  // words saying so, without scrolling (Ali, 2026-11-27,
  // inventory-tng-r3jx).
  useEffect(() => {
    if (done) {
      focusInPlace(registered.current);
      ignoreTaps(document, () => registered.current);
    }
  }, [done]);

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

  if (done) {
    return (
      <Page>
        <p role="status" ref={registered} tabIndex={-1}>
          {say(REGISTERED)}
        </p>
        {waiting.value > 0 && (
          <p>{say(BATCHES_WAITING, { count: waiting.value })}</p>
        )}
        <p>
          <a href="/scan">{say(GO_TO_SCAN)}</a>
        </p>
      </Page>
    );
  }

  const message = problem(outcome.value);
  const refused = phraseRefused(outcome.value);
  return (
    <Page>
      <p>{say(WHAT_REGISTERING_DOES)}</p>
      <form onSubmit={submit}>
        <Field label={say(PHRASE)}>
          {(id) => (
            <input
              id={id}
              name="code"
              inputMode="text"
              required
              autoComplete="off"
              autoCapitalize="none"
              spellcheck={false}
              aria-invalid={refused || undefined}
              aria-describedby={refused ? problemId : undefined}
              value={code.value}
              onInput={(event) => {
                code.value = event.currentTarget.value;
              }}
            />
          )}
        </Field>
        <button type="submit" class="primary" disabled={sending.value}>
          {say(sending.value ? REGISTERING : REGISTER)}
        </button>
      </form>
      {message !== null && (
        <p role="alert" id={problemId}>
          {say(message)}
        </p>
      )}
    </Page>
  );
}

function Page({ children }: { children: ComponentChildren }) {
  return (
    <main class="volunteer page-width">
      <h1>{say(REGISTER_THIS_DEVICE)}</h1>
      {children}
    </main>
  );
}
