/**
 * Making or correcting a place, where this batch is saying where it is.
 *
 * Why it hangs off that select is `./vocabulary.ts`. A dialog rather than the
 * panel `EditCategory` is, because what opens this is the column itself rather
 * than another dialog: decision 0025 point 4.
 *
 * TWO THINGS IT DELIBERATELY DOES NOT OFFER.
 *
 * A DELETE. The API exposes POST and PATCH here and nothing else, and a
 * location is referred to by the ledger for as long as the ledger exists.
 * `active` is the way out and it is on this form, worded as what it does —
 * the same word `EditItem` uses, for the same reason.
 *
 * A CUSTODY LOCATION. `Location.Kind.VOLUNTEER_CUSTODY` is a place a named
 * volunteer is holding stock, so `location_held_by_iff_custody` requires a
 * holder, `location_one_custody_per_volunteer` allows each person only one, and
 * `LocationSerializer.validate_held_by` requires that person still be offered
 * by the pick-list. None of that is a shelf somebody is standing at, and asking
 * for it here would put a second volunteer picker inside a dialog opened
 * mid-batch. It stays in Django's admin, which decision 0014 point 4 keeps for
 * exactly the cases this interface does not cover. The same reasoning is why
 * `kind` cannot be changed here: a custody row's kind is held by a constraint
 * this form has no way to satisfy.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import { apiPatch, apiPost } from "../api/client";
import type { Location } from "../api/types";
import { parentChoices, parentId, parentValue } from "./tree";
import { useSaving } from "./useSaving";

/**
 * The kinds of place this dialog will make.
 *
 * `Location.Kind` in backend/src/inventory/models.py is where these are
 * decided. Five of its six: `volunteer_custody` is left out for the reason in
 * this module's header, and leaving it out is what keeps the form free of the
 * holder it would have to ask for.
 */
const KINDS = [
  { value: "warehouse", label: "Warehouse" },
  { value: "hub", label: "Hub" },
  { value: "room", label: "Room" },
  { value: "shelf", label: "Shelf" },
  { value: "vehicle", label: "Vehicle" },
];

export function EditLocation({
  existing,
  locations,
  onClose,
  onSaved,
}: {
  /** The row being corrected, or null to make one. */
  existing: Location | null;
  /** The places already read by whoever opened this, so they are not read twice. */
  locations: Location[];
  onClose: () => void;
  onSaved: (location: Location) => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState(existing?.kind ?? KINDS[0].value);
  const [parent, setParent] = useState(() => parentValue(existing));
  const [active, setActive] = useState(existing?.active ?? true);
  const { saving, refusal, run } = useSaving();

  const save = () =>
    run(async () => {
      // The two fields both shapes carry. What each adds is on its own branch
      // below, so a create and a correction are each readable in one place.
      const shared = { name: name.trim(), parent: parentId(parent) };
      const saved =
        existing === null
          ? // `kind` only here. A PATCH carrying it would either re-send what
            // is already there or attempt the change this form cannot make
            // safely, and omitting it says plainly which of the two this is.
            await apiPost<Location>("/api/locations", { ...shared, kind })
          : // And `active` only when it CHANGED, which is not tidiness.
            // `LocationSerializer.validate` reads `active is True` as a revival
            // without comparing it to the stored value, so re-sending it on an
            // already-active custody row sends a rename down the revival branch
            // -- which refuses when the holder has since been merged, naming a
            // field this form deliberately does not carry. Saying nothing about
            // a field nobody touched is what keeps a rename a rename.
            await apiPatch<Location>(
              `/api/locations/${existing.id}`,
              active === existing.active ? shared : { ...shared, active },
            );
      onSaved(saved);
      onClose();
    });

  return (
    <Dialog open onClose={onClose} aria-labelledby="edit-location" fullWidth>
      <DialogTitle id="edit-location">
        {existing === null ? "Add a place" : `Edit ${existing.name}`}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            required
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            fullWidth
          />
          {existing === null ? (
            <TextField
              select
              label="What kind of place"
              value={kind}
              onChange={(event) => setKind(event.target.value)}
            >
              {KINDS.map((one) => (
                <MenuItem key={one.value} value={one.value}>
                  {one.label}
                </MenuItem>
              ))}
            </TextField>
          ) : null}
          <TextField
            select
            label="Inside"
            value={parent}
            onChange={(event) => setParent(event.target.value)}
            helperText="A site holds rooms and a room holds shelves."
          >
            {parentChoices(locations, existing?.id).map((one) => (
              <MenuItem key={one.value} value={one.value}>
                {one.label}
              </MenuItem>
            ))}
          </TextField>
          {existing !== null ? (
            <FormControlLabel
              control={
                <Switch checked={active} onChange={(event) => setActive(event.target.checked)} />
              }
              // Not "delete", for the reason EditItem gives about an item: the
              // ledger refers to this place for as long as the ledger exists,
              // and stock recorded here stays countable. Decision 0019.
              label="Offered in the pick-list"
            />
          ) : null}
          {refusal}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={saving || name.trim() === ""} onClick={save}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  );
}
