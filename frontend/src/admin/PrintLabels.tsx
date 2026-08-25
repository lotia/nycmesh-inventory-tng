/**
 * Minting stickers and putting them on a page, from the item list.
 *
 * THE ONE SURFACE DECISION 0025 WAS WRITTEN FOR. A sheet belongs to no row: it
 * spans items, and it is the whole reason that record exists. So this follows
 * it rather than inventing a shape of its own — opened from a control in the
 * one column beside the collection it is about, drawn over that column as a
 * dialog, full screen below the `sm` breakpoint because it is a list rather
 * than a form, and closing back to exactly the screen it was opened from.
 *
 * WHAT IT IS GATED ON, and why that is the honest gate. `print_label` is
 * `LabelListView` POST — minting — and `/api/labels/sheet` asks only for a
 * session. So a control that only laid out existing codes would be drawn from
 * a capability it never used, which is the thing decision 0014 point 3 forbids
 * in the other direction. Minting and printing are one act here because they
 * are one act at a shelf: nobody prints a sticker they have not made, and a
 * sticker nobody prints is a row.
 *
 * THE SHEET IS THE SERVER'S. `/api/labels/sheet` lays out the symbols, at the
 * sizes `inventory.labels` fixes and tests, and this hands the URL to the
 * browser to print. `LabelSheetView` says why that endpoint answers with a
 * document rather than with something for a client to arrange.
 *
 * WHAT IT DOES NOT DO. It does not narrow the estate by shelf or by print run —
 * `inventory-tng-3ez` is that question and it is the admin's. What is here is
 * the narrowing a print run actually needs: what you have just minted, and the
 * live codes for whatever you type.
 */
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import { useTheme } from "@mui/material/styles";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useState } from "react";
import { apiPost, searchPath } from "../api/client";
import type { Item, MappedLabel, Page, ResolvedLabel } from "../api/types";
import { useResource } from "../api/useResource";
import { describeQuantity, parseQuantity, toNumber } from "../items/quantity";
import { useSaving } from "./useSaving";

/**
 * As many as one page may carry, which is the server's number.
 *
 * `MAX_SHEET_LABELS` in backend/src/inventory/views.py, and it refuses above
 * it. Said here so the refusal is a disabled control and a sentence rather
 * than a page of prose where a sheet was expected.
 *
 * Deliberately not covered by a unit test. Reaching it means two hundred and
 * one rows and two hundred and one clicks through a list that re-renders on
 * each, which took seventy-four seconds and told nobody anything the line
 * above does not. The server refuses in any case, and its own test is where
 * that number is held.
 */
const MOST_ON_A_SHEET = 200;

/** How many stickers one press mints, at most. A run, not the estate. */
const MOST_AT_ONCE = 50;

export function PrintLabels({ onClose }: { onClose: () => void }) {
  const [search, setSearch] = useState("");
  const [item, setItem] = useState("");
  const [count, setCount] = useState("1");
  const [quantity, setQuantity] = useState("1");
  const [chosen, setChosen] = useState<string[]>([]);
  const [minted, setMinted] = useState(0);
  const { saving, refusal, run } = useSaving();
  const theme = useTheme();
  // Decision 0025 point 4: a list takes the screen on the phone this app is
  // laid out for, and is a panel over the column on anything wider.
  const wholeScreen = useMediaQuery(theme.breakpoints.down("sm"));

  const items = useResource<Page<Item>>(searchPath("/api/items", search));
  const offered = items.data?.results ?? [];
  // What the select is actually showing, which is not always what was chosen:
  // the search box narrows this list, and an item that falls out of it must
  // stop being the answer as well as stop being drawn. Reading `item` straight
  // back would leave the box blank over a stale id and mint a run for an item
  // nobody can see.
  const chosenItem = offered.some((one) => String(one.id) === item) ? item : "";
  // The unpaginated live map, which is the same one a scan resolves against
  // and already carries the item's name -- so nothing here holds the catalogue
  // as well. Re-read after a mint, which is what puts the new codes in it.
  const labels = useResource<MappedLabel[]>("/api/labels", minted);
  const known = labels.data ?? [];
  const onOffer = known.filter((one) => one.item !== null && matches(one, search));

  function toggle(code: string): void {
    setChosen((codes) =>
      codes.includes(code) ? codes.filter((one) => one !== code) : [...codes, code],
    );
  }

  const mint = () =>
    run(async () => {
      const wanted = Number(count);
      try {
        // One at a time: the API mints one code per POST, and each is drawn
        // and checked against the table by the server
        // (`Label.mint_unique_code`). Sequential rather than in parallel, so a
        // run that is refused half way stops there rather than sending fifty
        // writes at a refusal.
        for (let printed = 0; printed < wanted; printed += 1) {
          // `ResolvedLabel` and not a type of its own: `LabelSerializer` adds
          // exactly one field to the resolve shape and it is write-only, so
          // what a create answers with IS what a scan resolves to.
          const label = await apiPost<ResolvedLabel>("/api/labels", {
            item: Number(chosenItem),
            quantity,
          });
          // Ticked as it arrives rather than at the end of the run. A refusal
          // on the third of five leaves two rows in the database whatever this
          // does about them, so the only question is whether they are on the
          // sheet or are orphans nobody can see -- and the second answer
          // invites a second press, which mints two more.
          setChosen((codes) => [...codes, label.code]);
        }
      } finally {
        // Once, whether the run finished or stopped: the list below has to
        // show what was made either way, and re-reading it per sticker would
        // be fifty reads of an unpaginated endpoint for one press.
        setMinted((times) => times + 1);
      }
    });

  /** The sheet to ask for, or null when there is not one to ask for. */
  const sheet =
    chosen.length > 0 && chosen.length <= MOST_ON_A_SHEET
      ? `/api/labels/sheet?code=${encodeURIComponent(chosen.join(","))}`
      : null;

  const ready =
    chosenItem !== "" &&
    Number.isInteger(Number(count)) &&
    Number(count) > 0 &&
    Number(count) <= MOST_AT_ONCE &&
    (parseQuantity(quantity) ?? 0) > 0;

  return (
    <Dialog
      open
      onClose={onClose}
      aria-labelledby="print-labels"
      fullWidth
      fullScreen={wholeScreen}
    >
      <DialogTitle id="print-labels">Print labels</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField
            label="Search items"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            helperText="Narrows both lists below."
            fullWidth
          />

          <Typography variant="subtitle2" component="h3">
            Make new stickers
          </Typography>
          <TextField
            required
            select
            label="For"
            value={chosenItem}
            onChange={(event) => setItem(event.target.value)}
            disabled={offered.length === 0}
            error={items.error !== null}
            // Told apart the way CreateItem's category select tells them
            // apart, and for the reason `inventory-tng-3x7r` records: a read
            // that failed, one still in flight and a catalogue with nothing in
            // it all leave the box empty and mean different things.
            helperText={
              items.error
                ? items.error.message
                : items.loading
                  ? "Reading the catalogue…"
                  : offered.length === 0
                    ? "Nothing matches that."
                    : undefined
            }
          >
            {offered.map((one) => (
              <MenuItem key={one.id} value={String(one.id)}>
                {one.name}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            label="How many stickers"
            inputMode="numeric"
            value={count}
            onChange={(event) => setCount(event.target.value)}
            helperText={`One press makes up to ${MOST_AT_ONCE}.`}
          />
          <TextField
            label="What one scan stands for"
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
            // Decision 0011 section 5: the distinct quantities across an item's
            // labels *are* its packaging, so this is the number that decides
            // what a chip on the item row will offer.
            helperText="A box of a hundred is one sticker saying 100, not a hundred stickers."
          />
          <Button variant="outlined" disabled={saving || !ready} onClick={mint}>
            Make them
          </Button>
          {refusal}

          <Typography variant="subtitle2" component="h3">
            Put on the sheet
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {chosen.length === 0
              ? "Nothing on the sheet yet."
              : chosen.length > MOST_ON_A_SHEET
                ? `${chosen.length} on the sheet, and one page carries ${MOST_ON_A_SHEET}. Take some off.`
                : `${chosen.length} on the sheet.`}
          </Typography>
          {onOffer.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {labels.error
                ? labels.error.message
                : labels.loading
                  ? "Reading the labels…"
                  : "No live stickers match that."}
            </Typography>
          ) : (
            <List aria-label="Live labels" disablePadding>
              {onOffer.map((one) => (
                <ListItemButton key={one.code} onClick={() => toggle(one.code)} dense>
                  <Checkbox
                    edge="start"
                    tabIndex={-1}
                    disableRipple
                    checked={chosen.includes(one.code)}
                    slotProps={{ input: { "aria-label": `Put ${one.code} on the sheet` } }}
                  />
                  <ListItemText
                    primary={one.code}
                    // In the item's own unit, because 305 metres and 305 feet
                    // read alike on the one screen whose whole subject is what
                    // a scan of this sticker stands for. `items/quantity.ts`
                    // is where a quantity is said out loud.
                    secondary={`${one.item_name ?? "—"} · ${describeQuantity(
                      toNumber(one.quantity ?? "0"),
                      one.unit_of_measure ?? "each",
                      [],
                    )} a scan`}
                  />
                </ListItemButton>
              ))}
            </List>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {/* A link rather than a fetch, and only when there is a sheet to ask
            for. What comes back is a document for the browser to lay out and
            print, so it goes to the browser rather than through this app,
            which has nothing to do with it and no way to print it — and a
            disabled anchor is a control that says one thing and is another, so
            with nothing to ask for this is a plain button instead.

            The words never change, whatever is ticked and however many:
            the guide names this control, and a label that varies is a name
            nothing can check. What is on the sheet, and when there is too much
            of it, are both said on the line above where the count lives. */}
        {sheet === null ? (
          <Button variant="contained" disabled>
            Print the sheet
          </Button>
        ) : (
          <Button variant="contained" href={sheet} target="_blank" rel="noreferrer">
            Print the sheet
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

/** Whether this label is one of the ones the search box is asking about. */
function matches(label: MappedLabel, search: string): boolean {
  const typed = search.trim().toLowerCase();
  if (typed === "") {
    return true;
  }
  return (
    label.code.toLowerCase().includes(typed) ||
    (label.item_name ?? "").toLowerCase().includes(typed)
  );
}
