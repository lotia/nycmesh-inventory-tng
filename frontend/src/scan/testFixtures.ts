/**
 * Standing in for the camera hardware jsdom does not have.
 *
 * Only the enumeration half is worth simulating: what cameras a device says it
 * has is a plain list this app has to render and remember, and that is testable
 * without pretending to decode anything. Opening a stream is deliberately left
 * refusing, because a fake stream would only prove the fake works -- see the
 * header of CameraScanner.tsx.
 */

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
