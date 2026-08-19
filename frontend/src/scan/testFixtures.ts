/**
 * Standing in for the camera hardware jsdom does not have.
 *
 * Only the enumeration half is worth simulating: what cameras a device says it
 * has is a plain list this app has to render and remember, and that is testable
 * without pretending to decode anything. Opening a stream is deliberately left
 * refusing, because a fake stream would only prove the fake works -- see the
 * header of CameraScanner.tsx.
 */
import { vi } from "vitest";
import type { ResolvedLabel } from "../api/types";
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
  quantity: "1.000",
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
export function serving(label: unknown, item: unknown = zipTies, itemStatus = 200): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      // Honoured, because a caller that gave up is the case applyCode has to
      // re-throw rather than answer.
      if (init?.signal?.aborted) {
        throw new DOMException("aborted", "AbortError");
      }
      if (path.startsWith("/api/labels/")) {
        return new Response(JSON.stringify(label), { status: 200 });
      }
      return new Response(JSON.stringify(item), { status: itemStatus });
    }),
  );
}

/** The same, with the label read itself refused. */
export function refusing(detail: unknown, status: number): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(detail), { status })),
  );
}
