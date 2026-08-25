/**
 * Making or renaming a grouping, where an item is being put in one.
 *
 * Why it hangs off that select rather than off a categories screen is
 * `./vocabulary.ts`, which is also where the list and the re-read live.
 *
 * A PANEL RATHER THAN A DIALOG, and that is decision 0025 point 6 being obeyed
 * rather than worked around: nothing here opens a second surface over the first.
 * What opens this is already a dialog — the item being added — so this opens
 * *within* it, under the select it is about, and the item keeps everything that
 * has been typed into it. `EditLocation` is the same job opened from the column
 * instead, and is a dialog for that reason.
 *
 * THERE IS NO DELETE, and this says so rather than leaving somebody to find
 * out. The API exposes POST and PATCH on this collection and nothing else, and
 * a category carries no `active` column either (`CategoryDetailView` says why),
 * so an unwanted grouping is Django's admin — which decision 0014 point 4 keeps
 * for exactly this.
 */
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import { apiPatch, apiPost } from "../api/client";
import type { Category } from "../api/types";
import { parentChoices, parentId, parentValue } from "./tree";
import { useSaving } from "./useSaving";

export function EditCategory({
  existing,
  categories,
  onClose,
  onSaved,
}: {
  /** The row being corrected, or null to make one. */
  existing: Category | null;
  /** The groupings whoever opened this has already read, so they are not read twice. */
  categories: Category[];
  onClose: () => void;
  onSaved: (category: Category) => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [parent, setParent] = useState(() => parentValue(existing));
  const { saving, refusal, run } = useSaving();
  const heading = existing === null ? "Add a category" : `Rename ${existing.name}`;

  const save = () =>
    run(async () => {
      const body = { name: name.trim(), parent: parentId(parent) };
      const saved =
        existing === null
          ? await apiPost<Category>("/api/categories", body)
          : await apiPatch<Category>(`/api/categories/${existing.id}`, body);
      onSaved(saved);
      onClose();
    });

  return (
    <Paper variant="outlined" component="section" aria-label={heading} sx={{ p: 2 }}>
      <Stack spacing={2}>
        <Typography variant="subtitle2" component="h3">
          {heading}
        </Typography>
        <TextField
          required
          label="Category name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          fullWidth
        />
        <TextField
          select
          label="Inside"
          value={parent}
          onChange={(event) => setParent(event.target.value)}
          helperText="Categories nest. Leave this at the top level if it stands on its own."
        >
          {parentChoices(categories, existing?.id).map((one) => (
            <MenuItem key={one.value} value={one.value}>
              {one.label}
            </MenuItem>
          ))}
        </TextField>
        {existing !== null ? (
          <Typography variant="body2" color="text.secondary">
            A grouping has no way out of this list. Move its items to another one; an empty one can
            be removed in the admin.
          </Typography>
        ) : null}
        {refusal}
        <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
          <Button size="small" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="small"
            variant="contained"
            disabled={saving || name.trim() === ""}
            onClick={save}
          >
            Save category
          </Button>
        </Stack>
      </Stack>
    </Paper>
  );
}
