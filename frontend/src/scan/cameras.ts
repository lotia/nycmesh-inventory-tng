/**
 * Which camera, and whether there is one at all.
 *
 * The third non-optional constraint of
 * docs/decisions/0011-qr-batch-scanning.md section 2: `facingMode:
 * "environment"` is a request, not a guarantee, and iOS has answered it with
 * the ultra-wide lens (WebKit 253186), which cannot focus close enough to read
 * a small label. So the volunteer gets a list and picks, and what they picked
 * is remembered -- a phone that answers badly should cost somebody one tap
 * ever, not one tap per batch.
 */
import { isText, read, write } from "../storage";

/** Versioned, for the reason cartStorage's key is. */
export const STORAGE_KEY = "nycmesh-inventory.camera.v1";

export interface Camera {
  deviceId: string;
  label: string;
}

/**
 * Whether a camera can be opened here at all.
 *
 * `navigator.mediaDevices` is *undefined* on an insecure origin rather than
 * restricted, so a page served over plain HTTP looks like a bug rather than a
 * misconfiguration -- see the consequences in decision 0011. A desktop with no
 * camera answers the same way. Asked in one place only, and not to decide
 * whether to offer the camera: what a "no" is worth saying about is
 * CameraScanner.tsx's, and its header says why.
 */
export function cameraSupported(): boolean {
  return typeof navigator.mediaDevices?.getUserMedia === "function";
}

/**
 * Every video input, named.
 *
 * A device's `label` is empty until the user has granted permission at least
 * once, so the fallback is positional: "Camera 2" is still something a person
 * can pick by trying it.
 */
export async function listCameras(): Promise<Camera[]> {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices
    .filter((device) => device.kind === "videoinput")
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label === "" ? `Camera ${index + 1}` : device.label,
    }));
}

/**
 * The resolution asked for, so that the cost of a frame is knowable.
 *
 * `ideal`, never `exact`: a camera that cannot produce this must still open,
 * because the alternative is a volunteer with no scanner at all. Without it a
 * phone is free to answer with 1920x1080 or better, and every frame the
 * decoder is handed then costs whatever that device felt like -- the browser
 * decodes it, the compositor carries it, and the battery pays for it, all
 * before scan/frame.ts has scaled it down to something bounded.
 *
 * 1280 rather than the 640 frame.ts decodes at: this is what the volunteer
 * sees in the preview while lining a label up, and phone autofocus behaves
 * better in a mode the sensor actually has than in one it has to synthesise.
 */
export const IDEAL_WIDTH = 1280;

/**
 * What to ask `getUserMedia` for.
 *
 * With nothing remembered this asks for the back camera, which is right on
 * every phone that honours it and is the reason the picker exists for the ones
 * that do not. A remembered choice is `exact`: falling back to another lens
 * would silently undo the pick.
 */
export function constraints(deviceId: string | null): MediaStreamConstraints {
  return {
    video: {
      width: { ideal: IDEAL_WIDTH },
      ...(deviceId === null ? { facingMode: "environment" } : { deviceId: { exact: deviceId } }),
    },
  };
}

/**
 * Whether this is the browser saying the camera asked for is not there.
 *
 * Read by duck typing, for the reason `refusal` in CameraScanner.tsx gives.
 * `OverconstrainedError` is the answer to a remembered device id that no
 * longer exists, which is ordinary -- Safari mints new ones per session.
 */
export function cameraIsGone(error: unknown): boolean {
  const name = (error as Partial<Error> | null | undefined)?.name ?? "";
  return name === "NotFoundError" || name === "OverconstrainedError";
}

export function loadCamera(): string | null {
  return read(STORAGE_KEY, isText);
}

export function saveCamera(deviceId: string): void {
  write(STORAGE_KEY, deviceId);
}

/**
 * Forget the remembered camera, so the next open asks for the back one again.
 *
 * Stored as null rather than removed: ``loadCamera`` already ignores anything
 * that is not a device id, so nothing is gained by a second way of storing
 * nothing.
 */
export function forgetCamera(): void {
  write(STORAGE_KEY, null);
}
