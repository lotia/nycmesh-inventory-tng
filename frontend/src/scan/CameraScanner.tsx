/**
 * The camera, open and decoding.
 *
 * Deliberately thin: everything decidable lives beside this file in
 * cameras.ts, frame.ts, decodeLoop.ts and decoder.ts and is tested there, so
 * what is left here is the order things happen in. That order is itself a rule
 * -- the stream reaches the preview before it is played, and everything taken
 * is let go of however the open ends -- and CameraScanner.test.tsx asserts it
 * against a stream jsdom can be handed.
 *
 * What no test of this file can reach is the pixels: jsdom has no video
 * pipeline and no WebAssembly runtime, so whether a real frame decodes is
 * verified by pointing a phone at a sticker and by nothing automated -- see
 * the header of testFixtures.ts, which is where that is written down.
 */
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useEffect, useRef, useState } from "react";
import {
  type Camera,
  cameraIsGone,
  cameraSupported,
  constraints,
  forgetCamera,
  listCameras,
  loadCamera,
  saveCamera,
} from "./cameras";
import { decodeLoop } from "./decodeLoop";
import { loadDetector } from "./decoder";
import { frameGrabber } from "./frame";

/**
 * What is said when there is no camera API at all.
 *
 * `navigator.mediaDevices` is undefined on an insecure origin rather than
 * restricted, so the commonest cause of this message is the app being reached
 * over plain HTTP at a LAN address -- which is the natural way to try it from
 * a phone, and the reason decision 0011 lists TLS as a consequence.
 *
 * This component is the only place that asks whether a camera can be opened:
 * Scanner.tsx offers the button whatever the answer, precisely so that this
 * sentence reaches the person it was written for instead of their being shown
 * nothing at all.
 */
const NO_CAMERA =
  "No camera is available here. A camera needs the page to be served over HTTPS, so if you reached this app by its address on the network, that is why. Type the code printed under the QR instead, or find the item in the list.";

/**
 * Why the camera did not open, in terms that name the way out.
 *
 * Read by duck typing rather than `instanceof Error`: `getUserMedia` rejects
 * with a `DOMException`, which is not an `Error` subclass in the browsers this
 * runs in, so the name would be lost exactly where it matters.
 */
export function refusal(error: unknown): string {
  const failure = error as Partial<Error> | null | undefined;
  if (failure?.name === "NotAllowedError" || failure?.name === "SecurityError") {
    return "This browser is not letting the page use the camera. Type the code printed under the QR instead, or find the item in the list.";
  }
  if (cameraIsGone(error)) {
    return "That camera is not available. Pick another one, or type the code printed under the QR.";
  }
  return failure?.message ?? "The camera could not be opened.";
}

export function CameraScanner({ onCode }: { onCode: (code: string) => void }) {
  const supported = cameraSupported();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [deviceId, setDeviceId] = useState<string | null>(loadCamera);
  const [failure, setFailure] = useState<string | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  // `onCode` reaches the cart, whose dispatch changes identity on every scan,
  // so it changes on every scan too. Held in a ref rather than named as a
  // dependency below: restarting the camera after each decode would make the
  // second code of a batch impossible to scan.
  const latest = useRef(onCode);
  useEffect(() => {
    latest.current = onCode;
  });

  // Before permission every device is nameless (see listCameras), so the list
  // is asked for twice: once to know how many there are, and again once a
  // stream has been granted and they have names.
  useEffect(() => {
    if (!supported) {
      return;
    }
    // A device that will not enumerate is not a dead end: the picker is a way
    // past a phone that hands back the wrong lens, not a way in.
    listCameras()
      .then(setCameras)
      .catch(() => setCameras([]));
  }, [supported]);

  useEffect(() => {
    if (!supported) {
      return;
    }
    const media = navigator.mediaDevices;
    let stream: MediaStream | undefined;
    let stopDecoding: (() => void) | undefined;
    let live = true;

    /**
     * Let go of whatever has been taken so far.
     *
     * The cleanup below can only stop what exists when it runs, and the
     * volunteer taps "Stop camera" or switches lens while the permission
     * prompt is still up or the decoder is still downloading -- so the stream
     * arrives after it. `open` therefore checks `live` after each await it
     * holds something across and calls this when the answer is no, and the
     * catch below calls it too -- an open that failed has taken a stream just
     * as surely as one that succeeded. Without it the phone keeps the lens
     * powered and the tab keeps its recording indicator lit, which is what the
     * button was pressed to stop.
     */
    function release(): void {
      stopDecoding?.();
      for (const track of stream?.getTracks() ?? []) {
        track.stop();
      }
    }

    async function open(): Promise<void> {
      stream = await media.getUserMedia(constraints(deviceId));
      const element = video.current;
      if (!live || element === null) {
        release();
        return;
      }
      element.srcObject = stream;
      // Started before the preview, not after it: the decoder is a megabyte,
      // and this way it downloads while the volunteer is still being asked for
      // permission rather than afterwards, in front of a picture that decodes
      // nothing.
      const detecting = loadDetector();
      await element.play();
      // The second of the two enumerations described above, for the names
      // permission has now put on the devices. Neither awaited nor allowed to
      // fail the open: the names are a convenience and the decode loop below
      // is the point.
      listCameras()
        .then(setCameras)
        .catch(() => undefined);
      const detector = await detecting;
      if (!live) {
        release();
        return;
      }
      // One canvas for this stream, and a frame bounded before it is handed
      // over rather than the element handed straight to `detect`. What that
      // costs either way is measured in frame.ts.
      const grab = frameGrabber();
      stopDecoding = decodeLoop({
        detect: (source) => detector.detect(source),
        frame: () => grab(element),
        onCode: (code) => latest.current(code),
      });
    }

    open().catch((error: unknown) => {
      // Whatever went wrong, the lens is not being used any more. A failure
      // after the stream was granted -- `play` refused, the decoder's
      // megabyte lost on a basement connection -- would otherwise leave the
      // tracks live for the life of the mount, because `failure` is not a
      // dependency of this effect and nothing runs the cleanup below.
      release();
      // A camera torn down while it was opening rejects as a matter of course:
      // `play()` answers a stream that has just been stopped with an
      // AbortError. Saying so would put a warning on the screen about the lens
      // the volunteer has just left.
      if (!live) {
        return;
      }
      // A remembered camera that is no longer there is a dead end otherwise.
      // The picker is only offered where the device reports more than one, and
      // a browser that rotates device ids between sessions reports one until
      // permission has been granted -- so the choice is forgotten and the
      // default asked for instead, which is what the first visit got.
      if (deviceId !== null && cameraIsGone(error)) {
        forgetCamera();
        setDeviceId(null);
        return;
      }
      setFailure(refusal(error));
    });

    return () => {
      live = false;
      release();
    };
  }, [deviceId, supported]);

  function choose(chosen: string): void {
    saveCamera(chosen);
    setFailure(null);
    setDeviceId(chosen);
  }

  // Nothing else is drawn: a preview that will never show anything, and a
  // picker over a list that will always be empty, are furniture around a
  // sentence that is the whole content of this screen here.
  if (!supported) {
    return <Alert severity="warning">{NO_CAMERA}</Alert>;
  }

  return (
    <Stack spacing={1}>
      {/* Named rather than captioned: a live preview has no audio and nothing
          to transcribe, and the name is what a screen reader announces when
          focus reaches it. */}
      <Box
        component="video"
        ref={video}
        muted
        playsInline
        aria-label="Camera preview"
        sx={{ width: "100%", borderRadius: 1, backgroundColor: "action.hover" }}
      />
      {failure ? <Alert severity="warning">{failure}</Alert> : null}
      {cameras.length > 1 ? (
        <TextField
          select
          size="small"
          label="Camera"
          value={deviceId ?? ""}
          onChange={(event) => choose(event.target.value)}
          helperText="If what you see is too wide to focus on a label, try another one."
          fullWidth
        >
          {cameras.map((camera) => (
            <MenuItem key={camera.deviceId} value={camera.deviceId}>
              {camera.label}
            </MenuItem>
          ))}
        </TextField>
      ) : null}
    </Stack>
  );
}
