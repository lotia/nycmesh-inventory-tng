/**
 * Adding a catalogue entry from the catalogue.
 *
 * `EditItem` corrects an entry that already exists; this is its sibling, and
 * between them they are the two halves of `edit_catalogue` an item has. Before
 * it there was no way to add an item from this application at all, so setting
 * up a new shelf began by opening Django's admin — which is the application
 * decision 0014 exists to avoid.
 *
 * It hangs off the item *list* rather than off a row, because the item it makes
 * is not one of the rows there yet. Same capability, different anchor.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import { type ApiError, apiPost, asApiError } from "../api/client";
import type { Category, Item, Page } from "../api/types";
import { useResource } from "../api/useResource";
import { unitChoices } from "../items/quantity";
import { Refusal } from "./Refusal";

/**
 * What an item can be counted in, from the one place this app spells them out.
 *
 * `items/quantity.ts` transcribes `Item.UnitOfMeasure` and says what an unknown
 * unit does; a second list here would be the copy that gets edited alone.
 * Computed once at module load rather than per render: the set is fixed.
 */
const UNITS = unitChoices();

export function CreateItem({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [unit, setUnit] = useState(UNITS[0].value);
  const [minimum, setMinimum] = useState("0");
  const [reorder, setReorder] = useState("1");
  const [saving, setSaving] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);
  // Read when the dialog opens rather than with the item list: a volunteer
  // never sees this control, so nobody pays for the request who is not about
  // to use it.
  const { data, error, loading } = useResource<Page<Category>>("/api/categories");
  const categories = data?.results ?? [];

  // An item belongs to a category and the column is not nullable, so Add cannot
  // succeed without one. Not the client enforcing an invariant -- the server
  // still refuses, and `Refusal` below renders what it says -- but the same
  // move decision 0014 point 3 makes about a capability: a control that cannot
  // work is not offered. Both fields are marked `required` so the reason is on
  // the form rather than only in the absence of a way forward.
  const ready = name.trim() !== "" && category !== "";

  async function save(): Promise<void> {
    setSaving(true);
    setRefused(null);
    try {
      await apiPost<Item>("/api/items", {
        name: name.trim(),
        category: Number(category),
        unit_of_measure: unit,
        minimum_stock: minimum,
        reorder_quantity: reorder,
      });
      onCreated();
      onClose();
    } catch (error: unknown) {
      setRefused(asApiError(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} aria-labelledby="create-item" fullWidth>
      <DialogTitle id="create-item">Add an item</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            required
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            fullWidth
          />
          <TextField
            required
            select
            label="Category"
            // An empty string rather than null, for the reason LocationPicker
            // gives: a select with no value is uncontrolled.
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            disabled={categories.length === 0}
            error={error !== null}
            // In the order the states occur, so nothing has to be excluded
            // twice: a read that failed, then one still in flight, then a
            // catalogue that really is empty. The last is a claim about the
            // catalogue and must not be made before it has answered -- on the
            // connection decision 0011 section 1 exists for, that would send
            // somebody off to make a category that is already there.
            helperText={
              error
                ? error.message
                : loading
                  ? "Reading the categories…"
                  : categories.length === 0
                    ? "No categories yet. One has to exist before an item can join it."
                    : undefined
            }
          >
            {categories.map((one) => (
              <MenuItem key={one.id} value={String(one.id)}>
                {one.name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            label="Counted in"
            value={unit}
            onChange={(event) => setUnit(event.target.value)}
          >
            {UNITS.map((one) => (
              <MenuItem key={one.value} value={one.value}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
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
          {refused ? <Refusal error={refused} onDismiss={() => setRefused(null)} /> : null}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={saving || !ready} onClick={save}>
          Add
        </Button>
      </DialogActions>
    </Dialog>
  );
}
