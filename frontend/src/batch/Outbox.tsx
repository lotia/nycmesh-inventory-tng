/**
 * What the device is still holding, at the top of the screen where it cannot
 * be missed.
 *
 * A queue nobody can see is worse than no queue: the volunteer walks away
 * believing the stock was recorded. So every queued batch is drawn, waiting
 * ones and settled ones alike, and the news that one finally went in survives
 * a restart because the entry does -- somebody who saved in a basement and
 * closed the tab is told when they next open the app.
 *
 * What is queued and when it is sent is in outbox.ts.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useEffect, useSyncExternalStore } from "react";
import {
  discardBatch,
  outbox,
  type QueuedBatch,
  sendOutbox,
  subscribeToOutbox,
  waiting,
} from "./outbox";

/** The clock reading a volunteer can match against what they remember doing. */
function at(queuedAt: number): string {
  return new Date(queuedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function Held({ batch }: { batch: QueuedBatch }) {
  const { outcome } = batch;
  return (
    <Alert
      severity={outcome === null ? "info" : outcome.recorded ? "success" : "error"}
      action={
        <Button color="inherit" size="small" onClick={() => discardBatch(batch.key)}>
          {/* A batch the ledger has is news to dismiss. Anything else is work
              being thrown away, and the word has to say so. */}
          {outcome?.recorded ? "Dismiss" : "Discard"}
        </Button>
      }
    >
      {batch.what} ({at(batch.queuedAt)}) —{" "}
      {outcome === null
        ? "waiting to send."
        : outcome.recorded
          ? outcome.detail
          : `${outcome.detail} This batch will not be sent again.`}
    </Alert>
  );
}

export function Outbox() {
  const held = useSyncExternalStore(subscribeToOutbox, outbox);

  // Two triggers, neither of them a guarantee. Opening the app is the one that
  // always works, because something served the page. `online` is the browser's
  // opinion and it is only reliable in one direction: false means there is no
  // connection, true means there is an interface, which a captive portal and a
  // mesh node with no uplink both satisfy. A failed attempt costs nothing and
  // the batch stays queued, so guessing wrong is cheap; missing the moment the
  // network comes back is not, which is why the button below exists as well.
  useEffect(() => {
    void sendOutbox();
    const retry = (): void => {
      void sendOutbox();
    };
    window.addEventListener("online", retry);
    return () => window.removeEventListener("online", retry);
  }, []);

  if (held.length === 0) {
    return null;
  }

  return (
    <Stack component="section" aria-label="Batches not yet sent" spacing={1}>
      <Typography variant="subtitle2">Not yet sent</Typography>
      {held.map((batch) => (
        <Held key={batch.key} batch={batch} />
      ))}
      {waiting(held).length > 0 ? (
        <Button variant="outlined" onClick={() => void sendOutbox()}>
          Send now
        </Button>
      ) : null}
    </Stack>
  );
}
