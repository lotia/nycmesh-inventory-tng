/**
 * What the scan tests are built out of: fake hardware, fake timing, fixed data.
 *
 * The hardware half stands in for what jsdom has none of. What cameras a
 * device says it has is a plain list this app renders and remembers; a granted
 * stream is a bag of tracks somebody has to let go of, and letting go of them
 * -- on teardown, and when the stream arrives after it -- is a rule of this app
 * that two bugs were found in. The timing half (`deferred`) is here because
 * half of what these tests assert is about *when* something arrives, and the
 * moment has to be the test's to choose.
 *
 * WHAT NO TEST IN THIS DIRECTORY CLAIMS: that a QR code can be read. A fake
 * stream carries no pixels and a canned `rawValue` is a label reading "the
 * decoder said something", not evidence that it would. That question is
 * answered in a real browser, against a real symbol, by
 * integration/decodes.spec.ts -- and it is worth knowing that when it was
 * first asked the answer was no.
 */
import { type Mock, vi } from "vitest";
import type { Item, ResolvedLabel } from "../api/types";
import { zipTies } from "../items/testFixtures";

/** One video input, as `enumerateDevices` reports it. */
export function videoInput(deviceId: string, label = ""): MediaDeviceInfo {
  return { deviceId, label, kind: "videoinput", groupId: "" } as MediaDeviceInfo;
}

/** One microphone, so the filtering in listCameras has something to filter. */
export function audioInput(deviceId: string): MediaDeviceInfo {
  return { deviceId, label: "", kind: "audioinput", groupId: "" } as MediaDeviceInfo;
}

/**
 * Give this jsdom a `navigator.mediaDevices`.
 *
 * Defined on the navigator rather than stubbed globally: replacing the whole
 * navigator takes everything else on it with it, testing-library included.
 */
export function stubMediaDevices(devices: Partial<MediaDevices>): void {
  Object.defineProperty(navigator, "mediaDevices", { value: devices, configurable: true });
}

export function clearMediaDevices(): void {
  Reflect.deleteProperty(navigator, "mediaDevices");
}

/** A promise, and the two ways a test settles it when it chooses to. */
export interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

/**
 * A promise held open until the test says otherwise: a stream granted after
 * the scanner closed, a decoder that lands too late, a lens already left.
 *
 * `Promise.withResolvers` is exactly this in one line and is ES2024;
 * tsconfig.json targets ES2022 on purpose, and a test helper is not a reason
 * to move what the app is compiled against.
 */
export function deferred<T>(): Deferred<T> {
  // Assigned before `new Promise` answers -- the executor runs there and then
  // -- which TypeScript cannot see, and a placeholder pair of no-ops would be
  // two functions no test ever calls.
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((settle, fail) => {
    resolve = settle;
    reject = fail;
  });
  return { promise, resolve, reject };
}

/** A granted stream, and the tracks a test has to watch being stopped. */
export interface FakeStream {
  stream: MediaStream;
  /** One spy per track. The camera is only really off when every one is called. */
  stops: ReturnType<typeof vi.fn>[];
}

/**
 * A stream of two tracks, neither of which carries a single pixel.
 *
 * Two, because "every track is stopped" is the rule and one track cannot tell
 * it apart from "a track is stopped" -- and a spy each rather than one between
 * them, because a shared spy cannot tell it apart from one track stopped twice.
 */
export function fakeStream(): FakeStream {
  const stops = [vi.fn(), vi.fn()];
  const tracks = stops.map((stop) => ({ stop }));
  return { stream: { getTracks: () => tracks } as unknown as MediaStream, stops };
}

/**
 * The labels the scan tests resolve, in the shape `GET /api/labels/{code}`
 * answers with.
 *
 * Here rather than in each test file: three of them describe the same two
 * stickers, and a field added to `ResolvedLabel` should be one edit, not four.
 * Annotated rather than inferred, which is what makes that true: an untyped
 * literal goes on satisfying `unknown` however far it drifts from the type the
 * app parses it as.
 *
 * The two item codes match cart/testFixtures.ts, so a reader moving between
 * them is looking at one warehouse. The wall's is its own: one code cannot
 * mean a box of cable in one file and a shelf in another.
 */
export const PACKET: ResolvedLabel = {
  code: "7QK3M2XV9A",
  kind: "item",
  quantity: "100.000",
  revoked_at: null,
  item: 1,
  location: null,
};

export const WALL: ResolvedLabel = {
  code: "5RJ9T4HB2K",
  kind: "location",
  // Null, not the sentinel "1.000" this once carried: a wall code stands for
  // no quantity of anything, and the API cannot produce the old shape.
  quantity: null,
  revoked_at: null,
  item: null,
  location: 3,
};

/** A box of cable: measured, so a scan of it has to ask how much. */
export const CABLE_LABEL: ResolvedLabel = {
  ...PACKET,
  code: "4NP8R7T2WQ",
  quantity: "305.000",
  item: 2,
};

/**
 * The two reads a code costs: the label, then the item it points at.
 *
 * Every scan test stubs the same pair, so it is written once. The item
 * defaults to zip ties, which is what PACKET points at; `itemStatus` is for
 * the case where the label resolves and the item behind it does not.
 */
export function serving(
  label: unknown,
  item: unknown = zipTies,
  itemStatus = 200,
  // The signature and not `ReturnType<typeof vi.fn>`: what a test reads off
  // this is `mock.calls`, and the bare mock type makes every call an `any`
  // array -- so a path assertion written against the wrong shape would type
  // -check and then never match.
): Mock<(path: string, init?: RequestInit) => Promise<Response>> {
  const fetching = vi.fn(async (path: string, init?: RequestInit) => {
    // Honoured, because a caller that gave up is the case applyCode has to
    // re-throw rather than answer.
    if (init?.signal?.aborted) {
      throw new DOMException("aborted", "AbortError");
    }
    // The unpaginated lists the client caches, before the detail paths: the
    // detail check below matches a prefix and would otherwise swallow them.
    // Serving both here means any test can fill the cache from the same
    // fixture it already reads a single label out of.
    // The map, in the shape the server sends it. Serving it as a plain
    // ResolvedLabel let a revoked flag read off a missing field pass here.
    if (path === "/api/labels") {
      return new Response(JSON.stringify([mapped(label, item)]), { status: 200 });
    }
    if (path.startsWith("/api/labels/")) {
      return new Response(JSON.stringify(label), { status: 200 });
    }
    return new Response(JSON.stringify(item), { status: itemStatus });
  });
  vi.stubGlobal("fetch", fetching);
  // Answered as well as installed, so a test can say *what* was asked for --
  // which is how a code arriving wrapped in its own deep link was caught.
  return fetching;
}

/**
 * One row of the cached map, as LabelMapSerializer sends it.
 *
 * Not a ResolvedLabel: `revoked_at` is dropped, and the item's name and unit
 * ride along so the client need not hold the catalogue too. Exported so a test
 * can build a map without restating that shape.
 */
export function mapped(label: unknown, item: unknown = zipTies): Record<string, unknown> {
  const { revoked_at: _dropped, ...rest } = label as Record<string, unknown>;
  const behind = item as Partial<Item> | null;
  return {
    ...rest,
    item_name: behind?.name ?? null,
    unit_of_measure: behind?.unit_of_measure ?? null,
  };
}

/** The same, with the label read itself refused. */
export function refusing(detail: unknown, status: number): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(detail), { status })),
  );
}
