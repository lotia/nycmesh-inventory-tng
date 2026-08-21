/**
 * Who this batch is attributed to: searched first, created only if the search
 * found nobody.
 *
 * Every batch needs an actor, and the actor is a name picked from a list
 * rather than a credential -- docs/decisions/0012-two-populations.md point 5.
 * The order matters more than it looks: the sheet this replaces let anyone
 * type a name, and produced 102 spellings of fewer people. So the matches come
 * first and the "add me" option only after them.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useState } from "react";
import { type ApiError, apiPost, asApiError, refusalBody, searchPath } from "../api/client";
import type { Page, Volunteer, VolunteerConflict } from "../api/types";
import { useResource } from "../api/useResource";
import { useCart } from "../cart/CartProvider";
import { loadVolunteer, saveVolunteer } from "./rememberedVolunteer";

/**
 * Which column what was typed belongs in, if it is not a name.
 *
 * The search matches a name, an email address or a Slack ID, because a
 * volunteer who types any of the three and is shown nobody adds a duplicate --
 * the one thing this endpoint exists to prevent. So all three have to be
 * recognised here too: submitting an identifier as a `display_name` would put
 * it in the pick-list *as* somebody's name, which is the same duplicate in a
 * worse disguise.
 *
 * Deliberately crude. The question is not "is this a valid address" but "did
 * this person type their name or one of their identifiers", and nobody is
 * called anything@anything or U024BE7LH. The shapes are Slack's own -- a user
 * ID starts U or W -- and an "@" with something either side of it.
 */
export function identifierField(typed: string): "email" | "slack_id" | null {
  if (/^[^@\s]+@[^@\s]+$/.test(typed)) {
    return "email";
  }
  return /^[UW][A-Z0-9]{6,}$/.test(typed) ? "slack_id" : null;
}

/** The 409 body, if this refusal is one. See VolunteerConflict and refusalBody. */
function conflictIn(error: ApiError): VolunteerConflict | null {
  if (error.status !== 409) {
    return null;
  }
  return refusalBody<VolunteerConflict>(error, (body) => Boolean(body.volunteer && body.code));
}

export function VolunteerPicker() {
  const { cart, dispatch } = useCart();
  const [remembered, setRemembered] = useState(loadVolunteer);
  const [search, setSearch] = useState("");
  const [conflict, setConflict] = useState<VolunteerConflict | null>(null);
  const [name, setName] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { data, error, loading } = useResource<Page<Volunteer>>(
    searchPath("/api/volunteers", search),
  );

  const matches = data?.results ?? [];
  const typed = search.trim();
  const identifier = identifierField(typed);
  const ready = identifier === null || name.trim() !== "";
  const chosen = remembered?.id === cart.actorId ? remembered : null;

  function choose(volunteer: Volunteer): void {
    setRemembered(saveVolunteer(volunteer));
    // A different volunteer means a different batch as far as the server is
    // concerned, so the reducer mints a fresh idempotency key; see cartState.
    dispatch({ type: "setActor", actorId: volunteer.id });
    setSearch("");
    setName("");
    setConflict(null);
    setFailure(null);
  }

  /**
   * What a new volunteer is registered as.
   *
   * An identifier is submitted *as* that identifier, with the name asked for
   * separately -- see identifierField above for why, and for which shapes
   * count. It is also what lets the server answer the 409 of decision 0015:
   * that conflict only arises on a clash over `email` or `slack_id`, so a
   * client that only ever sent a display name could never provoke it.
   */
  function submission(): Record<string, string> {
    return identifier === null
      ? { display_name: typed }
      : { display_name: name.trim(), [identifier]: typed };
  }

  async function addMe(): Promise<void> {
    setSaving(true);
    setConflict(null);
    setFailure(null);
    try {
      choose(await apiPost<Volunteer>("/api/volunteers", submission()));
    } catch (error: unknown) {
      const refused = asApiError(error);
      // A clash with somebody the list will not show is not a dead end: the
      // 409 names who to continue as. Decision 0015.
      const clash = conflictIn(refused);
      if (clash) {
        setConflict(clash);
      } else {
        setFailure(refused.message);
      }
    } finally {
      setSaving(false);
    }
  }

  if (chosen) {
    return (
      <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
        <Typography>
          Working as <strong>{chosen.displayName}</strong>
        </Typography>
        <Button size="small" onClick={() => dispatch({ type: "setActor", actorId: null })}>
          Not you?
        </Button>
      </Stack>
    );
  }

  return (
    <Stack spacing={1}>
      <TextField
        label="Who are you?"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        fullWidth
      />

      <List aria-label="Volunteers" disablePadding>
        {matches.map((volunteer) => (
          <ListItemButton key={volunteer.id} onClick={() => choose(volunteer)}>
            <ListItemText
              primary={volunteer.display_name}
              secondary={volunteer.email ?? undefined}
            />
          </ListItemButton>
        ))}
      </List>

      {error ? <Alert severity="error">{error.message}</Alert> : null}

      {/* Only after the matches, and only once the search has *succeeded*:
          offering to add somebody because the search failed is how a network
          fault produces the duplicate this screen exists to prevent. */}
      {typed !== "" && !loading && !error ? (
        <Stack spacing={1}>
          {identifier !== null ? (
            <TextField
              label="And your name?"
              helperText={`${typed} is not in the list. What should it show?`}
              value={name}
              onChange={(event) => setName(event.target.value)}
              fullWidth
            />
          ) : null}
          <Button variant="outlined" disabled={saving || !ready} onClick={addMe}>
            {matches.length === 0 ? `Add ${typed}` : `Not listed — add ${typed}`}
          </Button>
        </Stack>
      ) : null}

      {conflict ? (
        <Alert severity="warning">
          <Typography variant="body2">{conflict.detail}</Typography>
          {conflict.selectable ? (
            <Button
              size="small"
              onClick={() => choose(conflict.volunteer)}
            >{`Continue as ${conflict.volunteer.display_name}`}</Button>
          ) : null}
        </Alert>
      ) : null}

      {failure ? <Alert severity="error">{failure}</Alert> : null}
    </Stack>
  );
}
