/**
 * How much of a measured item this scan was.
 *
 * Decision 0011 section 5: where `unit_of_measure` is anything but `each`, a
 * scan opens this and requires an entry. A cable label says what a full box
 * is, and somebody scanning one at a shelf is as likely to be returning part
 * of one -- so the label's own number is offered as a starting point and
 * never as an answer. Nothing is in the batch until this is submitted.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import { parseQuantity } from "../items/quantity";
import type { Measured } from "./applyCode";

export function MeasuredAmount({
  measured,
  onCancel,
  onEntered,
}: {
  measured: Measured;
  onCancel: () => void;
  onEntered: (quantity: number) => void;
}) {
  // Starts empty, not at the label's number. Prefilling it is the same
  // silent default in a box with a cursor in it -- the volunteer would tap
  // through and the ledger would say a full box either way.
  const [typed, setTyped] = useState("");
  const quantity = parseQuantity(typed);
  // Positive, mirroring stock_movement_quantity_positive: direction is which
  // side the location sits on, never the sign of the amount.
  const usable = quantity !== null && quantity > 0;

  function enter(): void {
    if (quantity !== null && usable) {
      onEntered(quantity);
    }
  }

  return (
    <Dialog open onClose={onCancel} aria-labelledby="measured-amount">
      <DialogTitle id="measured-amount">How much {measured.label.item.name}?</DialogTitle>
      <DialogContent>
        <Stack spacing={1} sx={{ pt: 1 }}>
          <TextField
            autoFocus
            type="text"
            inputMode="decimal"
            label={`Amount in ${measured.label.item.unitOfMeasure}`}
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                enter();
              }
            }}
          />
          <Typography variant="body2" color="text.secondary">
            A full one is {measured.label.quantity} {measured.label.item.unitOfMeasure}.
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="contained" disabled={!usable} onClick={enter}>
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
