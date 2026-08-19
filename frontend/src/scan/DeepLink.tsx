/**
 * What happens when a label's QR opened the app.
 *
 * Runs once, on load, and then gets out of the way: the code is applied to the
 * batch, the address bar is put back, and what happened is said in a line the
 * volunteer can read without leaving the shelf.
 *
 * Everything about what a code *does* is useScannedCode's, so arriving by
 * camera app and typing the same code by hand are the same event -- including
 * the beep, and including the question a measured item asks.
 */
import { useEffect, useState } from "react";
import { codeFromPath, forgetDeepLink } from "./deepLink";
import { MeasuredAmount } from "./MeasuredAmount";
import { OutcomeAlert } from "./outcome";
import { useScannedCode } from "./useScannedCode";

export function DeepLink() {
  const { outcome, measured, scan, enter, dismiss } = useScannedCode();
  // Read before the first render rather than on each one: the effect below
  // clears the address bar, so reading it there would race its own cleanup in
  // React's development double-invoke.
  const [code] = useState(() => codeFromPath(window.location.pathname));

  useEffect(() => {
    if (code === null) {
      return;
    }
    const controller = new AbortController();
    scan(code, controller.signal);
    forgetDeepLink();
    return () => controller.abort();
  }, [code, scan]);

  if (outcome === null) {
    return null;
  }
  return (
    <>
      {measured ? (
        <MeasuredAmount measured={measured} onCancel={dismiss} onEntered={enter} />
      ) : null}
      <OutcomeAlert outcome={outcome} onClose={dismiss} />
    </>
  );
}
