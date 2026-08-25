/**
 * Retiring a sticker where the sticker is.
 *
 * Decision 0014 point 1 names this one outright — "a label gains revocation" —
 * and the label is already on the screen: a code arrives by deep link, by
 * camera, by scanner gun or typed, and `OutcomeAlert` says what it did. The
 * control belongs on that sentence, because the person deciding a sticker is
 * finished is the person holding it.
 *
 * NOT A DELETE, and the word matters. The API exposes no DELETE on a label at
 * all, Django's admin no longer offers one either (`inventory-tng-ls6d`), and
 * this is the operation that is left: a PATCH of `revoked`, which the server
 * timestamps. `LabelSerializer` is why the client sends a boolean rather than
 * a moment.
 *
 * DESTRUCTIVE, so decision 0014 point 5 applies twice over and neither half is
 * this component's. `revoke_label` goes false the moment the session stops
 * being recent, so a stale administrator is drawn no control at all and
 * `StaleSession` at the top of the app offers the way back; and a session that
 * goes stale between the drawing and the pressing is refused by
 * `RecentlyAuthenticated`, which `Refusal` turns into the same prompt. What
 * this adds is the step in between: a confirmation naming the code, because a
 * scan is one tap and retiring a sticker should not be.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import { useState } from "react";
import { type ApiError, apiPatch, asApiError } from "../api/client";
import { forgetLabel } from "../scan/labelCache";
import { Refusal } from "./Refusal";
import { useCan } from "./SessionProvider";

export function RevokeLabel({ code, onRevoked }: { code: string; onRevoked: () => void }) {
  // Drawn from the server's answer, never guessed: decision 0014 point 3.
  const mayRevoke = useCan("revoke_label");
  const [asking, setAsking] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);

  if (!mayRevoke) {
    return null;
  }

  async function revoke(): Promise<void> {
    setSaving(true);
    setRefused(null);
    try {
      await apiPatch(`/api/labels/${encodeURIComponent(code)}`, { revoked: true });
      // Before the caller is told, so that whatever it re-reads cannot be
      // answered out of a map that still calls this sticker live.
      forgetLabel(code);
      onRevoked();
      setAsking(false);
    } catch (error: unknown) {
      setRefused(asApiError(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button size="small" onClick={() => setAsking(true)}>
        Revoke this label
      </Button>
      {asking ? (
        <Dialog open onClose={() => setAsking(false)} aria-labelledby="revoke-label" fullWidth>
          <DialogTitle id="revoke-label">Revoke {code}?</DialogTitle>
          <DialogContent>
            <DialogContentText>
              Scanning it will go on working and will say the sticker has been replaced, so nothing
              already recorded against it changes. Print a new label for whatever this was on.
            </DialogContentText>
            {refused ? <Refusal error={refused} onDismiss={() => setRefused(null)} /> : null}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setAsking(false)}>Keep it</Button>
            <Button variant="contained" disabled={saving} onClick={revoke}>
              Revoke it
            </Button>
          </DialogActions>
        </Dialog>
      ) : null}
    </>
  );
}
