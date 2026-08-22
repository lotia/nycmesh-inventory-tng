/**
 * What one code did, said in a line the volunteer can read at the shelf.
 *
 * Written once and used by every way a code arrives -- the deep link a phone's
 * own camera app opens, the in-app scanner, a wedge scanner and somebody
 * typing what is printed under a dead QR. They mean the same thing (see
 * applyCode.ts), so they say the same thing.
 */
import Alert from "@mui/material/Alert";
import type { Outcome } from "./applyCode";

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

export function OutcomeAlert({ outcome, onClose }: { outcome: Outcome; onClose: () => void }) {
  const { severity, text } = announcement(outcome);
  return (
    <Alert severity={severity} onClose={onClose}>
      {text}
    </Alert>
  );
}
