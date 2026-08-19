/**
 * The camera, open and decoding.
 *
 * Deliberately thin, because almost none of it can be tested honestly: jsdom
 * has no camera, no video pipeline and no WebAssembly decoder, so everything
 * from a granted permission onwards is verified by pointing a phone at a
 * sticker and not by a test that pretends to. What *is* testable lives beside
 * this file in cameras.ts and decoder.ts and is tested there -- which is the
 * reason this component holds wiring and nothing else.
 *
 * Every decode is handed on, including the same code five times a second. The
 * cart's reducer decides what is a new scan (`SCAN_DEBOUNCE_MS` in
 * cart/cartState.ts); a second opinion here would be a second thing to get
 * wrong.
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
import { loadDetector } from "./decoder";
import { frameGrabber } from "./frame";

/** Fast enough to feel instant, slow enough to leave the phone some battery. */
const DECODE_INTERVAL_MS = 200;

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
    let timer: ReturnType<typeof setInterval> | undefined;
    let live = true;

    /**
     * Let go of whatever has been taken so far.
     *
     * The cleanup below can only stop what exists when it runs, and the
     * volunteer taps "Stop camera" or switches lens while the permission
     * prompt is still up or the decoder is still downloading -- so the stream
     * and the timer arrive after it. `open` therefore checks `live` after each
     * await it holds something across and calls this when the answer is no.
     * Without it the phone keeps the lens powered and the tab keeps its
     * recording indicator lit, which is what the button was pressed to stop.
     */
    function release(): void {
      clearInterval(timer);
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
      setCameras(await listCameras());
      const detector = await detecting;
      if (!live) {
        release();
        return;
      }
      let busy = false;
      // One canvas for this stream, and a frame bounded before it is handed
      // over. Passing the video element straight to `detect` takes
      // barcode-detector's other path, which builds a fresh canvas and 2D
      // context per call at whatever resolution the camera answered with --
      // measured at 640x480 and this interval as ~7.7 MB/s of garbage, and
      // three times that from a 720p phone. A frame still has to reach the CPU
      // once a tick; what goes away is the canvas and context built per call,
      // and the growth with the lens. What is left is the ImageData
      // `getImageData` must return and the grayscale copy zxing-wasm makes of
      // it: at 640x360 that is 0.9 MB plus 0.2 MB, ~5.8 MB/s. See frame.ts.
      const grab = frameGrabber();
      timer = setInterval(async () => {
        if (busy || !live) {
          return;
        }
        busy = true;
        try {
          const found = await detector.detect(grab(element) ?? element);
          // Rechecked after the await as well: a decode that lands after the
          // volunteer closed the scanner must not add a line to the batch.
          if (!live) {
            return;
          }
          for (const code of found) {
            latest.current(code.rawValue);
          }
        } catch {
          // A frame that will not decode is the ordinary case, not an error:
          // most frames are a shelf, or a label mid-focus.
        }
        busy = false;
      }, DECODE_INTERVAL_MS);
      if (!live) {
        release();
      }
    }

    open().catch((error: unknown) => {
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
