/**
 * The label URL grammar: taking a code out of one, however it reached us.
 *
 * Labels encode a URL rather than a bare token, so a phone's own camera app --
 * which is how volunteers scan today, before this app has been opened at all --
 * deep-links into the app instead of showing a meaningless string. The path
 * shape is `/S/{code}` and it is minted in backend/src/inventory/labels.py;
 * see docs/decisions/0011-qr-batch-scanning.md sections 3 to 5.
 *
 * That URL reaches this app two ways, which is why both readers live here
 * rather than one of them beside whatever consumes it: in the address bar, on
 * arrival, and mid-session as the payload the in-app camera or a scanner gun
 * just decoded off a sticker.
 *
 * The first of those is one path, read once, with no router. A routing library
 * for a single entry point would be a dependency where three lines will do,
 * and docs/architecture.md makes adding one a decision to record rather than a
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
 * The code in something a scanner read, whatever shape it arrived in.
 *
 * The symbol on a label encodes the whole deep link, so a camera or a scanner
 * gun reading one hands over `HTTPS://INVENTORY.NYCMESH.NET/S/7QK3M2XV9A`
 * where a person reading the characters printed under it types
 * `7QK3M2XV9A`. Both are the same sticker and must mean the same thing --
 * decision 0011 section 3 chose a URL precisely so that a phone's own camera
 * app could follow it, and this is the other half of that choice.
 *
 * Only the path shape is matched, never the host. The client cannot know what
 * `LABEL_BASE_URL` this deployment prints, and a label printed against one
 * host and scanned by an app served from another is the ordinary case rather
 * than an attack -- the server decides whether the code exists, as it does for
 * a typed one. Anything that is not this shape is handed on untouched, so a QR
 * belonging to somebody else resolves to nothing and is reported as a code
 * this system does not know.
 */
export function codeFromScan(scanned: string): string {
  let address: URL;
  try {
    address = new URL(scanned);
  } catch {
    // Not a URL at all, which is what a typed or wedged code looks like.
    return scanned;
  }
  return codeFromPath(address.pathname) ?? scanned;
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
