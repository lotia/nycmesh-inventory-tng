/**
 * The badge that says this device is being recorded, and the way to stop.
 *
 * Non-negotiable rather than decorative. Recording is turned on by opening a
 * link somebody sent, so the volunteer holding the phone did not choose it and
 * may not know it happened; and it raises what the backend records about their
 * requests. A switch nobody can see is a switch nobody turns off.
 *
 * It says what it is in words rather than by an icon, and the control is
 * "Stop" rather than a settings screen two taps away.
 */

import Button from "@mui/material/Button";
import Snackbar from "@mui/material/Snackbar";
import { useState } from "react";

import { forget } from "./flag";
import { stop } from "./start";

export function Recording({ recording }: { recording: boolean }) {
  const [showing, setShowing] = useState(recording);
  if (!showing) {
    return null;
  }
  return (
    <Snackbar
      open
      anchorOrigin={{ vertical: "bottom", horizontal: "center" }}
      message="Recording this device for support. It stops on its own within the hour."
      action={
        <Button
          color="inherit"
          size="small"
          onClick={() => {
            forget();
            // The half that was missing. Clearing the token stops the header
            // on API calls, because `api/client.ts` re-reads the flag per
            // request -- but the provider went on batching spans and posting
            // them until the page was closed or the signature ran out, up to
            // an hour later, with the badge gone. `start.stop` is the teardown.
            void stop();
            setShowing(false);
          }}
        >
          Stop
        </Button>
      }
    />
  );
}
