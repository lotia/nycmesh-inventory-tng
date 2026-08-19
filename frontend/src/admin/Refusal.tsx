/**
 * What a refused administrative write says.
 *
 * Two refusals share a status code and mean opposite things: "this is not
 * yours" and "sign in again". The server distinguishes them with a code
 * (inventory/api.py), and this is where that distinction becomes two different
 * things on screen -- a prompt with somewhere to go, or a sentence.
 */
import Alert from "@mui/material/Alert";
import type { ApiError } from "../api/client";
import { needsSecondLook, StepUp } from "./StepUp";

export function Refusal({ error, onDismiss }: { error: ApiError; onDismiss: () => void }) {
  if (needsSecondLook(error)) {
    return <StepUp onDismiss={onDismiss} />;
  }
  return (
    <Alert severity="error" onClose={onDismiss}>
      {error.message}
    </Alert>
  );
}
