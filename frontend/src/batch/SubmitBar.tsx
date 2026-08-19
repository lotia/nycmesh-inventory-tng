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
import { useState } from "react";
import { type ApiError, apiPost, asApiError, refusalBody } from "../api/client";
import type { BatchError, BatchRejected, RecordedBatch } from "../api/types";
import { useCart } from "../cart/CartProvider";
import type { CartLine } from "../cart/cartState";
import { describeQuantity } from "../items/quantity";
import { LocationPicker } from "./LocationPicker";
import { batchBody, KINDS, sideFor, whatIsMissing } from "./movements";

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

export function SubmitBar() {
  const { cart, dispatch } = useCart();
  const [saving, setSaving] = useState(false);
  // Translated to item identity the moment it arrives; see byItem. Position
  // is the server's key and does not survive past the response, so fixing one
  // bad line leaves every other complaint attached to the row it was about.
  const [rejected, setRejected] = useState<{
    complaints: Map<number, string[]>;
    batch: string[];
    detail: string;
  } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [saved, setSaved] = useState<RecordedBatch | null>(null);

  const missing = whatIsMissing(cart);
  // A failed attempt leaves the batch where it was, so the button that follows
  // one says what it is: the same request again, under the same key.
  const again = rejected !== null || failure !== null;

  async function save(): Promise<void> {
    setSaving(true);
    setRejected(null);
    setFailure(null);
    // The last batch's success panel is not this batch's news; leaving it up
    // would put a green "Saved" above a red rejection.
    setSaved(null);
    try {
      // A 201 recorded it and a 200 means this exact batch was already
      // recorded -- the server matched the idempotency key. Both are success;
      // treating the second as anything else is how one save becomes two.
      const recorded = await apiPost<RecordedBatch>("/api/stock/transactions", batchBody(cart));
      setSaved(recorded);
      dispatch({ type: "clear" });
    } catch (error: unknown) {
      const refused = asApiError(error);
      const refusal = rejectionIn(refused);
      if (refusal) {
        setRejected({
          complaints: byItem(refusal.errors, cart.lines),
          batch: aboutTheBatch(refusal.errors),
          detail: refusal.detail,
        });
      } else {
        setFailure(refused.message);
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Stack spacing={2}>
      {saved ? (
        <Alert severity="success" onClose={() => setSaved(null)}>
          <AlertTitle>Saved</AlertTitle>
          {saved.warnings.length === 0 ? (
            "Recorded."
          ) : (
            <>
              {/* Advice, under a heading that says so. The movements were
                  recorded; this is what somebody should look at next. */}
              Recorded. Worth a stock count:
              <List dense disablePadding aria-label="Warnings">
                {saved.warnings.map((warning) => (
                  <ListItem key={`${warning.item}-${warning.location}`} disablePadding>
                    <ListItemText primary={warning.detail} />
                  </ListItem>
                ))}
              </List>
            </>
          )}
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
              const complaints = rejected?.complaints.get(line.itemId) ?? [];
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

      {rejected ? (
        <Alert severity="error">
          {rejected.batch.length > 0 ? rejected.batch.join(" ") : rejected.detail}
        </Alert>
      ) : null}
      {failure ? <Alert severity="error">{failure}</Alert> : null}

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
