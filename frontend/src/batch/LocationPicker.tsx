/**
 * Where this batch's stock is moving from or to.
 *
 * Scanning the code on the wall is the fast way and the one the mockup is
 * built around -- "only 1 QR code to scan" -- but it cannot be the only way.
 * Decision 0011 section 1 keeps a path that requires nothing: no camera, no
 * readable label, a desktop. A Save that could only be reached by scanning
 * would dead-end that path after the volunteer had already filled a batch,
 * and would make a deployment with no wall labels printed yet able to record
 * nothing at all.
 *
 * So this is the same choice offered as a list. A wall scan sets it too, and
 * whichever set it last is what shows.
 */
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import type { Location, Page } from "../api/types";
import { useResource } from "../api/useResource";
import { useCart } from "../cart/CartProvider";

export function LocationPicker() {
  const { cart, dispatch } = useCart();
  const { data, error } = useResource<Page<Location>>("/api/locations");
  const locations = data?.results ?? [];

  return (
    <TextField
      select
      label="Where the stock is"
      // An empty string rather than null: a select with no value is
      // uncontrolled, and React says so loudly in the console.
      value={cart.locationId === null ? "" : String(cart.locationId)}
      onChange={(event) =>
        dispatch({ type: "setLocation", locationId: Number(event.target.value) })
      }
      helperText={error ? error.message : "Or scan the code on the wall."}
      error={error !== null}
      disabled={locations.length === 0}
    >
      {locations.map((location) => (
        <MenuItem key={location.id} value={String(location.id)}>
          {location.name}
        </MenuItem>
      ))}
    </TextField>
  );
}
