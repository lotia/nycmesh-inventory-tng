/**
 * What the 409 of decision 0015 shows, and what it deliberately does not.
 *
 * THE SITUATION. Somebody searched the pick-list, was offered nobody, typed
 * their own address, and the server refused because a record the list will not
 * show already holds it. Decision 0015 chose to answer that with a 409 naming
 * whoever the searcher should be looking at rather than a 400 about a record
 * they cannot see, because the alternative is the dead end that produced 102
 * spellings of fewer people than that.
 *
 * WHAT IS RENDERED, and it is a short list on purpose.
 *
 * `detail` — the server's own sentence, unchanged. Which record may be named,
 * and in what words, is that endpoint's decision (0015 point 4) and not this
 * screen's to widen. It already contains the survivor's display name, which is
 * why the buttons below may repeat it.
 *
 * `selectable` — the constant, branched on rather than read, which is what
 * 0015 point 4 put it there for. `code` is deliberately unused: it is the
 * coarser of the two, since `volunteer_inactive` always implies `selectable`
 * is false and a merged survivor may be either, so branching on it as well
 * would be a second way of asking one question.
 *
 * WHAT IS NOT RENDERED, which matters more.
 *
 * NOT THE NAMED RECORD'S IDENTIFIERS. The body carries `volunteer` serialised
 * exactly as the pick-list serialises one, so it holds `email` and `slack_id` —
 * and the pick-list's own rows show the email underneath the name, so reusing
 * that row here is the obvious move and is the wrong one. 0015's consequences
 * weigh this surface as one volunteer's display name and identifiers going to
 * whoever submitted a matching address, and ask for it to be weighed again
 * later. Nothing here widens it in the meantime: what is shown is what `detail`
 * already said.
 *
 * NOT THE RECORD THAT HOLDS THE IDENTIFIER. `volunteer` is the end of the merge
 * chain (0015 point 1), not the row that literally holds the address. That row
 * is precisely the one the pick-list refuses to show, the API offers no way to
 * ask for it, and this never tries: every action below acts on the id the
 * server named.
 *
 * NOT WHICH FIELD CLASHED, beyond what `detail` says. `field` is machine
 * readable and this screen has nothing to do with it that the sentence has not
 * already done.
 *
 * THE SENTENCE IS NOT GATED; THE ACTIONS ARE. "Continue as them" is offered to
 * anybody, because it is the way out 0015 exists to provide and a volunteer is
 * exactly who needs it. Restoring a retired record is an administrator's act
 * (decision 0012 point 2), so it is drawn from `merge_volunteers` and from
 * nothing else — and it is the half that was missing, because until now the
 * sentence said "an administrator can restore it" to an administrator who then
 * had to go and open Django's admin.
 */
import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { apiPatch } from "../api/client";
import type { VolunteerConflict as Conflict, Volunteer } from "../api/types";
import { useCan } from "./SessionProvider";
import { useSaving } from "./useSaving";

export function VolunteerConflict({
  conflict,
  onContinueAs,
}: {
  conflict: Conflict;
  /** Work as this record, once there is a record that can be worked as. */
  onContinueAs: (volunteer: Volunteer) => void;
}) {
  const mayRestore = useCan("merge_volunteers");
  const { saving, refusal, run } = useSaving();

  const restore = () =>
    run(async () => {
      // `active`, and nothing else. Restoring is undoing a retirement; a body
      // that also cleared `merged_into` would be undoing a merge nobody asked
      // about, and the two are separate acts on separate records.
      const back = await apiPatch<Volunteer>(`/api/volunteers/${conflict.volunteer.id}`, {
        active: true,
      });
      // Straight into the batch, because being them is what the person was
      // trying to do when they met this.
      onContinueAs(back);
    });

  return (
    <Alert severity="warning">
      <Stack spacing={1} sx={{ alignItems: "flex-start" }}>
        <Typography variant="body2">{conflict.detail}</Typography>
        {conflict.selectable ? (
          <Button size="small" onClick={() => onContinueAs(conflict.volunteer)}>
            {`Continue as ${conflict.volunteer.display_name}`}
          </Button>
        ) : mayRestore ? (
          <Button size="small" disabled={saving} onClick={restore}>
            {`Restore ${conflict.volunteer.display_name}`}
          </Button>
        ) : null}
        {refusal}
      </Stack>
    </Alert>
  );
}
