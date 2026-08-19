/**
 * The catalogue, and the path that works with no camera and no readable label.
 *
 * This is the screen the volunteer's own mockup is almost entirely made of:
 * every item, its current count, and a way to add it to the batch. See
 * docs/decisions/0011-qr-batch-scanning.md.
 */
import Alert from "@mui/material/Alert";
import LinearProgress from "@mui/material/LinearProgress";
import List from "@mui/material/List";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useCallback, useMemo, useState } from "react";
import { searchPath } from "../api/client";
import type { Item, Page } from "../api/types";
import { useResource } from "../api/useResource";
import { useCart } from "../cart/CartProvider";
import { ItemRow } from "./ItemRow";

export function ItemList() {
  const { cart, dispatch } = useCart();
  const [search, setSearch] = useState("");
  // Read on every keystroke, deliberately: the list is one page of a small
  // catalogue on a local network, and a debounce would put a delay between a
  // volunteer typing and the shelf they are standing at appearing.
  // Bumped when a row is edited, which is what asks the hook to read the same
  // path again -- the count that was on screen is the one the edit changed.
  const [changed, setChanged] = useState(0);
  const { data, error, loading } = useResource<Page<Item>>(
    searchPath("/api/items", search),
    changed,
  );
  const items = data?.results ?? [];
  // One lookup for the page rather than one scan of the batch per row, and
  // one stable callback, so `ItemRow`'s memo has something to compare.
  const inBatch = useMemo(
    () => new Map(cart.lines.map((line) => [line.itemId, line.quantity])),
    [cart.lines],
  );
  const reread = useCallback(() => setChanged((count) => count + 1), []);

  return (
    <Stack spacing={2}>
      <TextField
        label="Search items"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        fullWidth
      />

      {/* Height reserved whether or not it is loading, so the list does not
          jump under a finger between keystrokes. */}
      <LinearProgress
        aria-label="Loading items"
        sx={{ visibility: loading ? "visible" : "hidden" }}
      />

      {error ? <Alert severity="error">{error.message}</Alert> : null}

      {!loading && !error && items.length === 0 ? (
        <Typography color="text.secondary">
          {search.trim() === "" ? "No items in the catalogue yet." : `Nothing matches “${search}”.`}
        </Typography>
      ) : null}

      <List aria-label="Items" disablePadding>
        {items.map((item) => (
          <ItemRow
            key={item.id}
            item={item}
            inCart={inBatch.get(item.id) ?? 0}
            dispatch={dispatch}
            onChanged={reread}
          />
        ))}
      </List>
    </Stack>
  );
}
