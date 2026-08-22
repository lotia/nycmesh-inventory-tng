/**
 * Correcting a catalogue entry where the entry already is.
 *
 * This dialog is decision 0014 point 1 applied to the catalogue. An
 * administrator who has just been looking at an item's count and packaging
 * edits it here rather than finding the same item under a different name in a
 * different application.
 *
 * Retiring is a change to `active`, not a delete: the ledger refers to the
 * item for as long as the ledger exists.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import { type ApiError, apiPatch, asApiError } from "../api/client";
import type { Item } from "../api/types";
import { Refusal } from "./Refusal";

export function EditItem({
  item,
  onClose,
  onSaved,
}: {
  item: Item;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(item.name);
  const [minimum, setMinimum] = useState(item.minimum_stock);
  const [reorder, setReorder] = useState(item.reorder_quantity);
  const [active, setActive] = useState(item.active);
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);

  async function save(): Promise<void> {
    setSaving(true);
    setRefused(null);
    try {
      await apiPatch<Item>(`/api/items/${item.id}`, {
        name,
        minimum_stock: minimum,
        reorder_quantity: reorder,
        active,
      });
      onSaved();
      onClose();
    } catch (error: unknown) {
      setRefused(asApiError(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby="edit-item" fullWidth>
      <DialogTitle id="edit-item">Edit {item.name}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            fullWidth
          />
          <TextField
            label="Minimum stock"
            inputMode="decimal"
            value={minimum}
            onChange={(event) => setMinimum(event.target.value)}
          />
          <TextField
            label="Reorder quantity"
            inputMode="decimal"
            value={reorder}
            onChange={(event) => setReorder(event.target.value)}
          />
          <FormControlLabel
            control={
              <Switch checked={active} onChange={(event) => setActive(event.target.checked)} />
            }
            // Not "delete": the word has to match what happens, which is that
            // the item stops being offered and stays in the ledger.
            label="Offered in the pick-list"
          />
          {refused ? <Refusal error={refused} onDismiss={() => setRefused(null)} /> : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={saving} onClick={save}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
