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
import { useSyncExternalStore } from "react";

import { forget } from "./flag";
import { recording, stop, subscribe } from "./start";

export function Recording() {
  // SUBSCRIBED, NOT COPIED. This took the answer as a prop and put it in
  // `useState` at mount, so it was a photograph of what was true when the page
  // rendered: the SDK could stop -- on its expiry -- and the badge would go on
  // saying otherwise, which is exactly the thing a switch nobody can see is
  // for. `start.ts` owns the answer and this asks it.
  const showing = useSyncExternalStore(subscribe, recording);
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
            // `start.stop` is the teardown, and the expiry takes the same one.
            // Nothing is set here afterwards: stopping is what makes the badge
            // go, through the subscription above.
            void stop();
          }}
        >
          Stop
        </Button>
      }
    />
  );
}
