/**
 * What one code did, said in a line the volunteer can read at the shelf.
 *
 * Written once and used by every way a code arrives -- the deep link a phone's
 * own camera app opens, the in-app scanner, a wedge scanner and somebody
 * typing what is printed under a dead QR. They mean the same thing (see
 * applyCode.ts), so they say the same thing.
 *
 * And it is where a sticker is retired, because it is the only moment a label
 * is on the screen at all -- decision 0014 point 1. `RevokeLabel` draws itself
 * or nothing, so a volunteer's line is exactly the line it was.
 */
import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import { RevokeLabel } from "../admin/RevokeLabel";
import { namesASticker, type Outcome } from "./applyCode";

/** The sentence for each outcome, and how loudly to say it. */
function announcement(outcome: Outcome): {
  severity: "success" | "warning" | "error";
  text: string;
} {
  switch (outcome.applied) {
    case "item":
      return {
        severity: outcome.revoked ? "warning" : "success",
        text: outcome.revoked
          ? `Added ${outcome.quantity} × ${outcome.name}. That sticker has been replaced — the one on the shelf should be reprinted.`
          : `Added ${outcome.quantity} × ${outcome.name}.`,
      };
    case "location":
      return {
        severity: outcome.revoked ? "warning" : "success",
        text: outcome.revoked
          ? "Location set. That sticker has been replaced — the one on the wall should be reprinted."
          : "Location set for this batch.",
      };
    case "measured":
      // Not a confirmation: nothing is in the batch yet, and saying so is the
      // point. Why nothing measured is defaulted is `countsItself`'s.
      return {
        severity: "warning",
        text: `${outcome.measured.label.item.name} is measured in ${outcome.measured.label.item.unitOfMeasure}. Say how much before it goes in the batch.`,
      };
    case "unknown":
      // Not an error page. Somebody is holding a label this system does not
      // know, and the useful answer is the catalogue below, not a status code.
      return {
        severity: "warning",
        text: `Nothing here is labelled ${outcome.code}. Search for the item instead.`,
      };
    case "failed":
      return { severity: "error", text: outcome.detail };
  }
}

/**
 * The code this outcome resolved, if it resolved one that is still live.
 *
 * Null for a sticker already revoked, which is what stops the control being
 * offered on a label with nothing left to give up. Which outcomes resolve one
 * at all is `namesASticker`'s.
 */
function liveCode(outcome: Outcome): string | null {
  return namesASticker(outcome) && !outcome.revoked ? outcome.code : null;
}

export function OutcomeAlert({
  outcome,
  onClose,
  onRevoked,
}: {
  outcome: Outcome;
  onClose: () => void;
  /**
   * Told that this code is no longer live, so the line above can say so.
   *
   * Required, and that is the point: a caller that could leave it out would
   * revoke a sticker and go on telling the person holding it that the scan was
   * fine, which is the one thing this control exists not to do.
   */
  onRevoked: (code: string) => void;
}) {
  const { severity, text } = announcement(outcome);
  const code = liveCode(outcome);
  return (
    <Alert severity={severity} onClose={onClose}>
      <Stack spacing={1} sx={{ alignItems: "flex-start" }}>
        <span>{text}</span>
        {code === null ? null : <RevokeLabel code={code} onRevoked={() => onRevoked(code)} />}
      </Stack>
    </Alert>
  );
}
