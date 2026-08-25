/**
 * Saying that two of these are one person, where the two are on screen.
 *
 * Decision 0014 point 1 names this one: "the volunteer picker gains merging".
 * The picker is where somebody types a name and is shown two of them, and that
 * is the only moment the pair is in front of anybody — a people-management
 * screen would be a place to go and look for a problem that has already
 * announced itself here.
 *
 * WHICH WAY ROUND IS THE WHOLE FORM, so it asks in those words rather than in
 * the model's. `merged_into` is set on the DUPLICATE and points at the
 * survivor; getting it backwards is not an error the database can catch,
 * because both directions are legal edits. So the two selects are labelled by
 * what happens to each record, the sentence between them says it again, and
 * neither is filled in for you.
 *
 * WHAT IT DOES NOT DO. Nothing is rewritten: every movement the duplicate is
 * already on stays exactly where it is, which is the reason a merge is an edit
 * rather than a migration (docs/data-model.md). And it is not final — emptying
 * the field puts the record back — so this asks once and does not lecture.
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { useState } from "react";
import { apiPatch } from "../api/client";
import type { Volunteer } from "../api/types";
import { useSaving } from "./useSaving";

/**
 * One end of the sentence: which of these people, said in what happens to them.
 *
 * Written once and called twice, so that the two calls read as the two halves
 * of the direction this dialog is about rather than as one control with a
 * different label. Not `inventory-tng-3x7r`'s shared select: that one reads a
 * paged collection and has a failure and an in-flight state to say something
 * about, and this takes a list it was handed.
 */
function Which({
  label,
  value,
  onChange,
  candidates,
}: {
  label: string;
  value: string;
  onChange: (id: string) => void;
  candidates: Volunteer[];
}) {
  return (
    <TextField
      required
      select
      label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {candidates.map((one) => (
        <MenuItem key={one.id} value={String(one.id)}>
          {one.display_name}
        </MenuItem>
      ))}
    </TextField>
  );
}

export function MergeVolunteers({
  candidates,
  onClose,
  onMerged,
}: {
  /** The people the search turned up, which is the pair somebody is looking at. */
  candidates: Volunteer[];
  onClose: () => void;
  onMerged: () => void;
}) {
  const [duplicate, setDuplicate] = useState("");
  const [survivor, setSurvivor] = useState("");
  const { saving, refusal, run } = useSaving();
  // Both chosen, and not the same one. The server refuses that too -- "a
  // volunteer cannot be merged into themselves" -- but a select cannot be the
  // way somebody finds out which of two identical names they picked twice.
  const ready = duplicate !== "" && survivor !== "" && duplicate !== survivor;

  const merge = () =>
    run(async () => {
      await apiPatch<Volunteer>(`/api/volunteers/${duplicate}`, {
        merged_into: Number(survivor),
      });
      onMerged();
      onClose();
    });

  return (
    <Dialog open onClose={onClose} aria-labelledby="merge-volunteers" fullWidth>
      <DialogTitle id="merge-volunteers">Two of these are the same person</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <Which
            label="Stop offering this one"
            value={duplicate}
            onChange={setDuplicate}
            candidates={candidates}
          />
          <Which
            label="Keep this one"
            value={survivor}
            onChange={setSurvivor}
            candidates={candidates}
          />
          <DialogContentText variant="body2">
            The first record stops being offered to anybody. Everything already recorded against it
            stays exactly where it is, and nothing changes number. It can be undone from the admin.
          </DialogContentText>
          {refusal}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={saving || !ready} onClick={merge}>
          Merge
        </Button>
      </DialogActions>
    </Dialog>
  );
}
