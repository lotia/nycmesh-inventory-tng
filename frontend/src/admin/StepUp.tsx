/**
 * The prompt somebody entitled to a change sees when the server wants a
 * second look first.
 *
 * Decision 0014 point 5 makes re-authentication a requirement of putting
 * administrative capability in the volunteer app. What that costs, from where
 * the administrator is standing, is one interruption -- so the interruption
 * has to say what it is for and come back to what they were doing, rather than
 * dropping them at a sign-in page with no memory of why.
 *
 * allauth owns the form itself. This only sends them to it and back.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import { useState } from "react";
import { type ApiError, refusalBody } from "../api/client";

/** The code the server puts on this refusal and no other. See inventory/api.py. */
const REAUTHENTICATION = "reauthentication_required";

import { useCurrentSession } from "./SessionProvider";

/** Whether this refusal is the one a second sign-in fixes. */
export function needsSecondLook(error: ApiError): boolean {
  if (error.status !== 403) {
    return false;
  }
  return refusalBody<{ code: string }>(error, (body) => body.code === REAUTHENTICATION) !== null;
}

/**
 * Where allauth's re-authentication form lives, told where to come back to.
 *
 * The current URL, so the volunteer returns to the screen they were on. The
 * app has no router, so "the screen they were on" is the page itself.
 */
function reauthenticateAt(): string {
  return `/accounts/reauthenticate/?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
}

export function StepUp({ onDismiss }: { onDismiss: () => void }) {
  return (
    <Alert severity="info" onClose={onDismiss}>
      <AlertTitle>Sign in again to make this change</AlertTitle>
      Editing the catalogue, merging volunteers, revoking labels and printing new ones ask once
      more, even inside a session that is already signed in.
      <Button size="small" href={reauthenticateAt()}>
        Sign in again
      </Button>
    </Alert>
  );
}

/**
 * The same prompt, offered before anything is pressed.
 *
 * Without it a stale session is a dead end. A capability says what the caller
 * may do *now*, so `edit_catalogue` goes false the moment the session goes
 * stale and every administrative control disappears -- and the refusal that
 * would have offered the way back never happens, because there is nothing left
 * to press. `recently_authenticated` is what separates the two (decision 0014
 * points 3 and 5): still an administrator, just not lately.
 */
export function StaleSession() {
  const me = useCurrentSession();
  const [dismissed, setDismissed] = useState(false);

  if (!me.administrator || me.recently_authenticated || dismissed) {
    return null;
  }
  return <StepUp onDismiss={() => setDismissed(true)} />;
}
