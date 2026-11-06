/**
 * The camera, for scanning labels (Scanner.tsx), with a QR decoder
 * (decoder.ts). Needs a real camera, so browser/camera.spec.ts tests it,
 * not Vitest.
 */
import { qrDetector } from "./decoder";

/** A camera opened onto a video element, and what it can read. */
export type Session = {
  /** The codes in view now. */
  read(): Promise<string[]>;
  /** Turns the torch on or off; null where the camera has none. */
  torch: ((on: boolean) => Promise<void>) | null;
  stop(): void;
};

/** The camera opened, but the decoder did not load: offline, say. */
export class DecoderUnavailable extends Error {}

/** Opens the rear camera onto `video`. Rejects if it is refused or absent. */
export async function openCamera(video: HTMLVideoElement): Promise<Session> {
  // Loading while the volunteer answers the camera's prompt.
  const decoder = qrDetector().catch((error: unknown) => {
    throw new DecoderUnavailable(String(error));
  });
  // Not left unhandled if the camera is refused first.
  decoder.catch(() => {});
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: "environment" } },
    audio: false,
  });
  const stop = () => {
    for (const track of stream.getTracks()) {
      track.stop();
    }
  };
  try {
    video.srcObject = stream;
    await video.play();
    const detector = await decoder;
    const [track] = stream.getVideoTracks();
    const capabilities = track?.getCapabilities?.() as
      | (MediaTrackCapabilities & { torch?: boolean })
      | undefined;
    return {
      read: async () =>
        (await detector.detect(video)).map((code) => code.rawValue),
      torch:
        track !== undefined && capabilities?.torch === true
          ? (on) =>
              track.applyConstraints({
                advanced: [{ torch: on } as MediaTrackConstraintSet],
              })
          : null,
      stop,
    };
  } catch (error) {
    stop();
    throw error;
  }
}
