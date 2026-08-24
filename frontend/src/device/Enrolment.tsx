/**
 * The screen a device meets before the app will answer it.
 *
 * PROVISIONAL. This is act four and act five of the access-posture demo, and
 * it is the whole of what the room is being asked to judge: not what the
 * credential is -- `device/credential.ts` is clear that it is an opaque token
 * and not a security control -- but what standing in front of this screen
 * costs, on a second phone, on a laptop, and on a roof with no node up yet.
 * inventory-tng-81f7.4 deletes it once inventory-tng-81f7 has chosen.
 *
 * WHY IT SITS INSIDE `<SessionProvider>` AND WRAPS MOST OF THE APP. Every
 * screen reads through `api/client.ts`, and under an enrolling posture every
 * one of those reads is a 403 until the device has a token. Gating each screen
 * would draw six failures where the honest answer is one question; gating here
 * asks it once and lets the app be itself afterwards. What is deliberately
 * OUTSIDE it -- the outbox -- is named in `App.tsx`.
 *
 * It draws no heading and no container of its own: `App.tsx` owns the page,
 * and a second title under the first is a screen that looks broken.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import { type ReactNode, useState } from "react";
import { useSessionAnswered } from "../admin/SessionProvider";
import { apiPost, asApiError } from "../api/client";
import { ENROL_AT, remember } from "./credential";

/** What `POST /api/devices` answers with. See `DeviceCredentialSerializer`. */
interface Credential {
  token: string;
}

const COULD_NOT_STORE =
  "This browser would not keep the registration, so it cannot be set up. A private window blocks it.";

export function Enrolment({ needsCode }: { needsCode: boolean }) {
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [enrolling, setEnrolling] = useState(false);

  async function enrol(): Promise<void> {
    setEnrolling(true);
    setFailure(null);
    try {
      const credential = await apiPost<Credential>(
        ENROL_AT,
        needsCode ? { code: code.trim() } : {},
      );
      // STORED BEFORE ANYTHING IS BELIEVED, and the return value is read for
      // the reason `storage.write` documents: the token is minted once and
      // never handed back, so a browser that would not keep it has to say so
      // rather than reload into this same screen for ever.
      if (!remember(credential.token)) {
        setFailure(COULD_NOT_STORE);
        return;
      }
      // A fresh load, because `useSession` reads `/api/me` once and this is
      // exactly the kind of change it says comes back as one -- and because
      // everything the app fetched while it was being refused has to be asked
      // for again.
      window.location.reload();
    } catch (error: unknown) {
      setFailure(asApiError(error).message);
    } finally {
      setEnrolling(false);
    }
  }

  return (
    <Stack spacing={3}>
      <Alert severity="info">
        <AlertTitle>Set this device up</AlertTitle>
        {needsCode
          ? "This inventory answers devices that have been registered with the code handed out at the hub. Type it once and this device is remembered."
          : "This inventory registers each device once before it will answer it. Nothing is asked of you beyond this."}
      </Alert>
      {needsCode ? (
        <TextField
          label="Enrolment code"
          value={code}
          onChange={(event) => setCode(event.target.value)}
          fullWidth
        />
      ) : null}
      <Button
        variant="contained"
        disabled={enrolling || (needsCode && code.trim() === "")}
        onClick={enrol}
      >
        Set this device up
      </Button>
      {failure ? <Alert severity="error">{failure}</Alert> : null}
    </Stack>
  );
}

/**
 * The app, or the screen that has to happen first.
 *
 * Only an explicit `self` or `code` puts the screen up. Every other answer --
 * including the one this app falls back to when `/api/me` could not be read --
 * draws the app, which is what a deployment that asks for no device gets and
 * what every deployment gets today.
 *
 * NOTHING AT ALL UNTIL THE ANSWER ARRIVES, which is the correction this
 * needed. The fallback is `not_required`, so the first paint mounted the whole
 * application -- the picker, the catalogue, the scanner -- and every one of
 * their reads was refused before `/api/me` resolved and the gate swapped them
 * out. The screen this exists to replace with one question was drawn anyway,
 * as a screenful of red, in front of the room the demo was convened for.
 */
export function EnrolmentGate({ children }: { children: ReactNode }) {
  // The answer itself and not the fallback, which is the whole of the
  // correction: `useSessionAnswered` is null only while nothing has arrived.
  const me = useSessionAnswered();

  if (me === null) {
    return null;
  }
  if (me.enrolment !== "self" && me.enrolment !== "code") {
    return children;
  }
  return <Enrolment needsCode={me.enrolment === "code"} />;
}
