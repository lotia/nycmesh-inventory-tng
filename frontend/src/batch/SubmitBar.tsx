/**
 * One Save button, and what it says afterwards.
 *
 * Three things this deliberately does not do. It does not throw the batch away
 * when a save fails -- the volunteer scanned those lines standing at a shelf,
 * and the idempotency key was minted when the cart opened, so Retry is safe
 * however far the failed attempt got. It does not show a wall of text for a
 * rejection: each error carries the position of the line it is about, so it is
 * shown against that line. And it does not treat a warning as a failure -- a
 * negative balance is a prompt to run a stock count, and an interface that
 * calls it an error sends volunteers back to inventing corrections.
 *
 * The two failures are answered differently, and the difference is whether
 * anything answered at all. A refusal is something the volunteer can act on
 * here, so the batch stays in the cart with the complaints against its lines.
 * Nothing at the other end is not: it hands the batch to the outbox and gives
 * the cart back empty, so the next armful of stock can be scanned while the
 * last one waits for a signal. See outbox.ts.
 *
 * See docs/decisions/0011-qr-batch-scanning.md.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState, useSyncExternalStore } from "react";
import { type ApiError, apiPost, asApiError, refusalBody } from "../api/client";
import {
  type BatchError,
  type BatchRejected,
  type BatchWarning,
  isRecordedBatch,
} from "../api/types";
import { useCart } from "../cart/CartProvider";
import type { CartLine } from "../cart/cartState";
import { describeQuantity } from "../items/quantity";
import { LocationPicker } from "./LocationPicker";
import { batchBody, KINDS, sideFor, whatIsIn, whatIsMissing } from "./movements";
import { outbox, queueBatch, sendOutbox, subscribeToOutbox, waiting } from "./outbox";
import { RECORDED, WORTH_A_COUNT } from "./recorded";

/**
 * The per-line complaints, keyed by the item they are about.
 *
 * The server answers by position in the movements it was sent, which is the
 * only thing it knows -- but a volunteer fixing the first bad line changes
 * that array, and every complaint after it would then be attached to the
 * wrong row. So the position is translated once, here, into the identity the
 * client has: the movements are `cart.lines` in order, and a line is its item.
 */
function byItem(errors: BatchError[], lines: CartLine[]): Map<number, string[]> {
  const complaints = new Map<number, string[]>();
  for (const error of errors) {
    const line = error.index === null ? undefined : lines[error.index];
    if (line === undefined) {
      continue;
    }
    complaints.set(line.itemId, [...(complaints.get(line.itemId) ?? []), error.detail]);
  }
  return complaints;
}

/** The complaints about the batch itself rather than about any one line. */
function aboutTheBatch(errors: BatchError[]): string[] {
  return errors.filter((error) => error.index === null).map((error) => error.detail);
}

/** The rejection body, if this refusal is one. See refusalBody. */
function rejectionIn(error: ApiError): BatchRejected | null {
  return refusalBody<BatchRejected>(error, (body) => Array.isArray(body.errors));
}

/** When the device itself will not hold the batch. See `queueBatch`. */
const NOT_HELD =
  "This phone would not store the batch, so it is still here. Free some space, or try again once there is a signal.";

/**
 * What the last Save came to. One of four, never two.
 *
 * Held as a single value because the four are alternatives, and four
 * independent pieces of state are four things to remember to put down: the one
 * that stayed up would draw a green "Saved" over a red rejection, or leave the
 * button offering to try again after the batch had gone. Held apart, that is a
 * bug a new arm of this screen introduces by omission and no test catches; held
 * together, the type will not express it.
 *
 * `recorded` carries only what is drawn: an answer this app could not read is
 * still a recorded batch, and there is no id to invent for it. `rejected`
 * carries complaints already translated to item identity -- see byItem, and
 * why position does not survive the response. `queued` carries the key it
 * handed the outbox, which is what the notice is looked up by.
 */
type SaveOutcome =
  | { kind: "recorded"; warnings: BatchWarning[] }
  | { kind: "rejected"; complaints: Map<number, string[]>; batch: string[]; detail: string }
  | { kind: "failed"; detail: string }
  | { kind: "queued"; key: string };

export function SubmitBar() {
  const { cart, dispatch, handOver } = useCart();
  const [saving, setSaving] = useState(false);
  const [outcome, setOutcome] = useState<SaveOutcome | null>(null);

  // The cart empties when a batch is queued, so something has to say where it
  // went. The outbox panel lists it, but it is at the top of the screen and
  // the volunteer is looking at the bottom of it, having just pressed Save.
  //
  // Asked of the queue rather than remembered, so the notice goes when the
  // batch does. A flag set at Save would still be saying "waiting to send"
  // after the outbox had sent it and written the news above.
  const held = useSyncExternalStore(subscribeToOutbox, outbox);
  const queued =
    outcome?.kind === "queued" && waiting(held).some((batch) => batch.key === outcome.key);

  const missing = whatIsMissing(cart);
  // A failed attempt leaves the batch where it was, so the button that follows
  // one says what it is: the same request again, under the same key.
  const again = outcome?.kind === "rejected" || outcome?.kind === "failed";

  async function save(): Promise<void> {
    setSaving(true);
    // The last batch's news is not this batch's, and one value is one thing to
    // put down: a green "Saved" left above a red rejection was a reset away.
    setOutcome(null);
    // Built once, so what is queued after a failure is the request that
    // failed rather than a second rendering of a cart that may have moved on.
    const body = batchBody(cart);
    try {
      // A 201 recorded it and a 200 means this exact batch was already
      // recorded -- the server matched the idempotency key. Both are success;
      // treating the second as anything else is how one save becomes two.
      const answer = await apiPost<unknown>("/api/stock/transactions", body);
      // An answer this cannot read is still a batch the server took, so it is
      // announced as one -- with nothing to advise, because nothing legible
      // came back to advise about. What must not happen is the cart emptying
      // with no word at all, which is a recorded batch and a lost one wearing
      // the same face.
      setOutcome({ kind: "recorded", warnings: isRecordedBatch(answer) ? answer.warnings : [] });
      handOver();
      // A save that got through is the best evidence there is that the
      // network is back -- better than `online`, which only says an interface
      // exists. Anything queued in the basement goes now.
      void sendOutbox();
    } catch (error: unknown) {
      const refused = asApiError(error);
      if (refused.offline) {
        // Nothing answered, so there is nothing here for the volunteer to
        // fix. The batch keeps its key and goes to the outbox, which is what
        // makes replaying it safe; the cart comes back empty for the next one.
        if (!queueBatch({ key: cart.idempotencyKey, body, what: whatIsIn(cart) })) {
          // Nowhere to put it. The cart is the only copy there is, so it stays
          // exactly as it is and the volunteer is told why.
          setOutcome({ kind: "failed", detail: NOT_HELD });
          return;
        }
        setOutcome({ kind: "queued", key: cart.idempotencyKey });
        handOver();
        return;
      }
      const refusal = rejectionIn(refused);
      setOutcome(
        refusal
          ? {
              kind: "rejected",
              complaints: byItem(refusal.errors, cart.lines),
              batch: aboutTheBatch(refusal.errors),
              detail: refusal.detail,
            }
          : { kind: "failed", detail: refused.message },
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Stack spacing={2}>
      {outcome?.kind === "recorded" ? (
        <Alert severity="success" onClose={() => setOutcome(null)}>
          <AlertTitle>Saved</AlertTitle>
          {outcome.warnings.length === 0 ? (
            RECORDED
          ) : (
            <>
              {/* Advice, under a heading that says so. The movements were
                  recorded; this is what somebody should look at next. The
                  wording is recorded.ts's, which the queue says in one line. */}
              {WORTH_A_COUNT}
              <List dense disablePadding aria-label="Warnings">
                {outcome.warnings.map((warning) => (
                  <ListItem key={`${warning.item}-${warning.location}`} disablePadding>
                    <ListItemText primary={warning.detail} />
                  </ListItem>
                ))}
              </List>
            </>
          )}
        </Alert>
      ) : null}

      {queued ? (
        <Alert severity="info" onClose={() => setOutcome(null)}>
          <AlertTitle>Waiting to send</AlertTitle>
          Nothing answered, so this batch is being held on this phone and will go in when the
          network is back. It is listed at the top of the screen until it does.
        </Alert>
      ) : null}

      {cart.lines.length > 0 ? (
        <>
          <TextField
            select
            label="What is happening"
            value={cart.kind}
            onChange={(event) =>
              dispatch({
                type: "setKind",
                kind: event.target.value as (typeof KINDS)[number]["kind"],
              })
            }
          >
            {KINDS.map(({ kind, label }) => (
              <MenuItem key={kind} value={kind}>
                {label}
              </MenuItem>
            ))}
          </TextField>

          {sideFor(cart.kind) !== null ? <LocationPicker /> : null}

          <List aria-label="This batch" disablePadding>
            {cart.lines.map((line) => {
              const complaints =
                outcome?.kind === "rejected" ? (outcome.complaints.get(line.itemId) ?? []) : [];
              return (
                <ListItem key={line.itemId} divider disablePadding sx={{ py: 1 }}>
                  <ListItemText
                    // Spelled out, in the item's own unit, on the last screen
                    // before Save -- which is the screen the packet ambiguity
                    // would do the most damage on. See describeQuantity.
                    primary={`${line.name} — ${describeQuantity(line.quantity, line.unitOfMeasure, [])}`}
                    secondary={complaints.length > 0 ? complaints.join(" ") : undefined}
                    slotProps={{ secondary: { color: "error" } }}
                  />
                </ListItem>
              );
            })}
          </List>
        </>
      ) : null}

      {/* Under the lines rather than over them, because a rejection is read
          alongside the rows it marked. Only one of these two can be drawn:
          they are arms of the same value. */}
      {outcome?.kind === "rejected" ? (
        <Alert severity="error">
          {outcome.batch.length > 0 ? outcome.batch.join(" ") : outcome.detail}
        </Alert>
      ) : null}
      {outcome?.kind === "failed" ? <Alert severity="error">{outcome.detail}</Alert> : null}

      {missing ? (
        <Typography color="text.secondary">{missing}</Typography>
      ) : (
        <Button variant="contained" size="large" disabled={saving} onClick={save}>
          {again ? "Try again" : "Save"}
        </Button>
      )}
    </Stack>
  );
}
