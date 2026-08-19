/**
 * The code a label's QR brought somebody here with.
 *
 * Labels encode a URL rather than a bare token, so a phone's own camera app --
 * which is how volunteers scan today, before this app has been opened at all --
 * deep-links into the app instead of showing a meaningless string. The path
 * shape is `/S/{code}` and it is minted in backend/src/inventory/labels.py;
 * see docs/decisions/0011-qr-batch-scanning.md sections 4 and 5.
 *
 * One path, read once, with no router. A routing library for a single entry
 * point would be a dependency where three lines will do, and
 * docs/architecture.md makes adding one a decision to record rather than a
 * thing to do quietly.
 */

/**
 * The code in this path, or null if the path is not a deep link.
 *
 * Both cases accepted: the minted payload is uppercase so the QR encoder can
 * use alphanumeric mode, but a code typed by hand or rewritten by something in
 * between arrives however it arrives. The server normalises the code itself
 * (Label.normalise_code), so this only has to recognise the shape.
 */
export function codeFromPath(pathname: string): string | null {
  const match = /^\/[Ss]\/([^/?#]+)\/?$/.exec(pathname);
  if (match === null) {
    return null;
  }
  try {
    return decodeURIComponent(match[1]);
  } catch {
    // `decodeURIComponent` answers a half-typed escape -- `/S/AB%` -- by
    // throwing, and this is read during render, so letting it out would blank
    // the whole app. The segment as it stands is the better answer: it
    // resolves to nothing and the volunteer is told the code is not ours,
    // which is what a mistyped address should look like.
    return match[1];
  }
}

/**
 * Put the address bar back to the app's own.
 *
 * Without this a reload re-applies the scan, and a volunteer who pulls to
 * refresh gets a second line they did not ask for. `replaceState` rather than
 * `pushState`: the label is how they arrived, not somewhere to go back to.
 */
export function forgetDeepLink(): void {
  window.history.replaceState(null, "", "/");
}
