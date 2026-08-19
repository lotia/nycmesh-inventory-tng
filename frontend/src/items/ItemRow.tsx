/**
 * One catalogue row: what there is, how much of it, and how to add some.
 *
 * The stepper and the chips write straight to the cart, and the row's number
 * is the line already in it -- so "add" and "how much am I taking" are one
 * control rather than two. See docs/decisions/0011-qr-batch-scanning.md
 * section 5.
 */
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import ListItem from "@mui/material/ListItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import type { Item } from "../api/types";
import { useCart } from "../cart/CartProvider";
import { describeQuantity, formatQuantity, toNumber } from "./quantity";

/**
 * The distinct quantities on an item's live labels, which *are* its packaging.
 *
 * One of them is offered as a chip beside the stepper, because a bare +1 is
 * not usable for something stocked in the hundreds. A label meaning one is not
 * offered: that is what the stepper already does.
 */
function packetSizes(item: Item): number[] {
  const sizes = new Set(item.labels.map((label) => toNumber(label.quantity)).filter((q) => q > 1));
  return [...sizes].sort((a, b) => a - b);
}

/** Everything on a shelf anywhere, which is what the mockup shows per row. */
function onHand(item: Item): number {
  return item.balances.reduce((total, balance) => total + toNumber(balance.quantity), 0);
}

/**
 * The cart line's quantity, typed rather than stepped.
 *
 * The half-typed text is held here rather than round-tripped through the cart,
 * because two of the states a person passes through on the way to a number are
 * not numbers. "1." on the way to "1.5" reads back as `1` and would erase the
 * point the moment it was typed -- and a measured item is exactly what this
 * field is for. An empty field reads back as `0`, and a quantity of zero takes
 * the line out of the cart, so clearing the box to retype it would delete the
 * line from under the cursor.
 *
 * So the cart is written only when what has been typed is a number, and the
 * draft is dropped on blur, at which point the cart is the truth again.
 */
function QuantityField({
  item,
  quantity,
  onChange,
}: {
  item: Item;
  quantity: number;
  onChange: (quantity: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);

  return (
    <TextField
      size="small"
      type="text"
      inputMode="decimal"
      value={draft ?? formatQuantity(quantity)}
      label="Quantity"
      // On the input, not the wrapper: every row has a field labelled
      // "Quantity", so the name a screen reader announces has to say
      // which item's. The visible word is inside it, so the two agree.
      slotProps={{ htmlInput: { "aria-label": `Quantity of ${item.name} in the batch` } }}
      sx={{ width: "8rem" }}
      onChange={(event) => {
        const typed = event.target.value;
        setDraft(typed);
        const parsed = Number(typed);
        if (typed.trim() !== "" && Number.isFinite(parsed)) {
          onChange(parsed);
        }
      }}
      onBlur={() => setDraft(null)}
    />
  );
}

export function ItemRow({ item }: { item: Item }) {
  const { cart, dispatch } = useCart();
  const line = cart.lines.find((candidate) => candidate.itemId === item.id);
  const inCart = line?.quantity ?? 0;
  const packets = packetSizes(item);

  const add = (quantity: number) =>
    dispatch({
      type: "add",
      item: { id: item.id, name: item.name, unitOfMeasure: item.unit_of_measure },
      quantity,
    });

  const setQuantity = (quantity: number) =>
    dispatch({ type: "setQuantity", itemId: item.id, quantity });

  return (
    <ListItem divider sx={{ display: "block", py: 2 }}>
      <Stack
        direction="row"
        spacing={2}
        sx={{ justifyContent: "space-between", alignItems: "baseline" }}
      >
        <Typography variant="subtitle1" component="h3">
          {item.name}
        </Typography>
        <Typography variant="body2" color="text.secondary" noWrap>
          {formatQuantity(onHand(item))} on hand
        </Typography>
      </Stack>

      <Stack direction="row" spacing={1} sx={{ mt: 1, alignItems: "center", flexWrap: "wrap" }}>
        <IconButton
          size="small"
          aria-label={`Remove one ${item.name}`}
          disabled={inCart === 0}
          onClick={() => setQuantity(inCart - 1)}
        >
          −
        </IconButton>
        <Box component="span" aria-live="polite" sx={{ minWidth: "2.5rem", textAlign: "center" }}>
          {formatQuantity(inCart)}
        </Box>
        <IconButton size="small" aria-label={`Add one ${item.name}`} onClick={() => add(1)}>
          +
        </IconButton>
        {packets.map((size) => (
          <Chip
            key={size}
            clickable
            size="small"
            label={`+${formatQuantity(size)}`}
            aria-label={`Add a packet of ${formatQuantity(size)} ${item.name}`}
            onClick={() => add(size)}
          />
        ))}
      </Stack>

      {line ? (
        <Stack direction="row" spacing={2} sx={{ mt: 1.5, alignItems: "center" }}>
          <QuantityField item={item} quantity={line.quantity} onChange={setQuantity} />
          <Typography variant="body2">
            {describeQuantity(line.quantity, item.unit_of_measure, packets)}
          </Typography>
        </Stack>
      ) : null}
    </ListItem>
  );
}
