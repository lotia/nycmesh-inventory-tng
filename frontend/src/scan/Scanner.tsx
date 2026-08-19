/**
 * Filling the batch by code: typed, wedged or seen.
 *
 * Three inputs, one path. A USB or Bluetooth scanner gun types the code and
 * presses Enter, somebody reading the characters printed under a dead QR types
 * the same thing, and the camera decodes it -- and all three end in
 * `applyCode`, which is also where a label's own deep link ends. One
 * resolution means faded ink and a denied camera degrade to a slower way of
 * doing the same thing rather than to a dead end. See
 * docs/decisions/0011-qr-batch-scanning.md section 1.
 *
 * The camera is the only part of this that is not always here: it costs a
 * permission, a secure context and a megabyte of decoder, so it is behind a
 * button and is not offered at all where it cannot work.
 */
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import { CameraScanner } from "./CameraScanner";
import { cameraSupported } from "./cameras";
import { MeasuredAmount } from "./MeasuredAmount";
import { OutcomeAlert } from "./outcome";
import { useScannedCode } from "./useScannedCode";

export function Scanner() {
  const { outcome, measured, scan, enter, dismiss } = useScannedCode();
  const [typed, setTyped] = useState("");
  const [camera, setCamera] = useState(false);
  return (
    <Stack spacing={1}>
      <form
        aria-label="Scan a code"
        onSubmit={(event) => {
          event.preventDefault();
          scan(typed);
          // Cleared whatever the answer: a scanner gun sends the next code
          // straight after this one, into whatever is in the box.
          setTyped("");
        }}
      >
        <Stack direction="row" spacing={1}>
          {/* Focused on arrival because a wedge scanner types into whatever
              has focus and has no way to ask for any. */}
          <TextField
            label="Scan or type a code"
            helperText="A scanner gun types here. So can you: the code is printed under the QR."
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            autoFocus
            fullWidth
            size="small"
          />
          {cameraSupported() ? (
            <Button type="button" variant="outlined" onClick={() => setCamera(!camera)}>
              {camera ? "Stop camera" : "Camera"}
            </Button>
          ) : null}
        </Stack>
      </form>

      {camera ? <CameraScanner onCode={scan} /> : null}
      {measured ? (
        <MeasuredAmount measured={measured} onCancel={dismiss} onEntered={enter} />
      ) : null}

      {outcome ? <OutcomeAlert outcome={outcome} onClose={dismiss} /> : null}
    </Stack>
  );
}
