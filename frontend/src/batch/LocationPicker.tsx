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
 *
 * AND IT IS WHERE A PLACE IS MADE. An administrator standing at a shelf that is
 * not in this list is the moment decision 0014 point 1 is about, so the control
 * for making one is here rather than on a locations screen -- gated on
 * `edit_catalogue`, so a volunteer's own screen is exactly what it was.
 */
import Button from "@mui/material/Button";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { EditLocation } from "../admin/EditLocation";
import { useCan } from "../admin/SessionProvider";
import { useVocabulary } from "../admin/vocabulary";
import type { Location } from "../api/types";
import { useCart } from "../cart/CartProvider";

export function LocationPicker() {
  const { cart, dispatch } = useCart();
  // Drawn from the server's answer, never guessed: decision 0014 point 3.
  const mayEdit = useCan("edit_catalogue");
  const places = useVocabulary<Location>("/api/locations", (saved) =>
    // Only a place the list still offers. Clearing "Offered in the pick-list"
    // is a save like any other, and taking it as the answer to "where is this
    // stock" would leave the batch pointing at a row the next read of this
    // list will not carry -- an empty select, a Save still enabled, and the
    // server refusing the whole batch at `stock_movement_to_location_is_active`.
    dispatch({ type: "setLocation", locationId: saved.active ? saved.id : null }),
  );
  const { rows: locations, error } = places;
  const chosen = locations.find((one) => one.id === cart.locationId) ?? null;

  return (
    <Stack spacing={1}>
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

      {mayEdit ? (
        <Stack direction="row" spacing={1}>
          <Button size="small" onClick={places.add}>
            New place
          </Button>
          {chosen ? (
            <Button size="small" onClick={() => places.correct(chosen)}>
              Edit {chosen.name}
            </Button>
          ) : null}
        </Stack>
      ) : null}

      {places.editing ? (
        <EditLocation
          existing={places.editing.row}
          locations={locations}
          onClose={places.close}
          onSaved={places.settled}
        />
      ) : null}
    </Stack>
  );
}
