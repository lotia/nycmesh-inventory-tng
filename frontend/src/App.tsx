import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useEffect } from "react";
import { SessionProvider } from "./admin/SessionProvider";
import { StaleSession } from "./admin/StepUp";
import { Outbox } from "./batch/Outbox";
import { SubmitBar } from "./batch/SubmitBar";
import { CartProvider } from "./cart/CartProvider";
import { EnrolmentGate } from "./device/Enrolment";
import { ItemList } from "./items/ItemList";
import { DeepLink } from "./scan/DeepLink";
import { refreshLabelCache } from "./scan/labelCache";
import { Scanner } from "./scan/Scanner";
import { VolunteerPicker } from "./volunteers/VolunteerPicker";

/**
 * The volunteer app.
 *
 * The whole flow designed in docs/decisions/0011-qr-batch-scanning.md: the
 * code a label's QR opened the app with, who the batch is attributed to, the
 * three ways to fill it -- scanned, wedged or typed above, browsed in the
 * catalogue below -- and the Save that sends it. What each of them is for, and
 * what is built and what is not, is listed in docs/architecture.md.
 *
 * Both providers are here rather than at the root so that a test renders the
 * app and gets the app -- the batch in hand, and who is signed in, which is
 * what decides whether the administrative controls are drawn at all.
 */
/**
 * The label map and the catalogue, once, so a scan resolves without a round
 * trip from a basement -- which is what decision 0011 section 6 asks for and
 * what the unpaginated endpoints exist to serve. Deliberately not awaited and
 * deliberately not shown: a volunteer can scan before it lands, and a failure
 * only means the scanner is slower. See scan/labelCache.ts.
 *
 * OUTSIDE THE ENROLMENT GATE, which is where it was moved to and then back
 * from. Behind the gate a device that has not enrolled makes one fewer refused
 * read -- but the gate withholds until `/api/me` answers, so EVERY deployment,
 * including every one that sets no posture at all, waited a round trip before
 * the prefetch started. That is precisely the basement decision 0011 section 6
 * is about. The read it saves is unawaited and never shown, so its failing
 * costs nobody anything they can see; the delay costs the case this exists for.
 *
 * A component rather than an effect on `App` because `App` now renders no
 * effect of its own, and a lone `useEffect` above a tree of children reads as
 * something the tree depends on.
 */
function Prefetched() {
  useEffect(() => {
    const stop = new AbortController();
    void refreshLabelCache(stop.signal);
    return () => stop.abort();
  }, []);
  return null;
}

export default function App() {
  return (
    <SessionProvider>
      <Container maxWidth="sm" sx={{ py: 4 }}>
        <Stack spacing={3}>
          <Typography variant="h4" component="h1">
            NYC Mesh Inventory
          </Typography>
          {/* OUTSIDE THE GATE, and deliberately. A batch this device is still
              holding is the one thing on this screen somebody may walk away
              without having seen, and the moments it most needs drawing are
              exactly the ones the gate would swallow -- a device revoked
              mid-shift, or site data cleared. Behind the gate it stopped being
              drawn AND stopped retrying, because the effect that sends it never
              ran. See batch/Outbox.tsx. */}
          <Outbox />
          <Prefetched />
          <EnrolmentGate>
            <CartProvider>
              <Stack spacing={3}>
                {/* Once, at the top, rather than in place of each control it
                  removed: a stale session takes every administrative control
                  away at the same moment, and one prompt is what fixes all of
                  them. Nothing is drawn for a volunteer. */}
                <StaleSession />
                <DeepLink />
                <VolunteerPicker />
                <Scanner />
                <ItemList />
                <SubmitBar />
              </Stack>
            </CartProvider>
          </EnrolmentGate>
        </Stack>
      </Container>
    </SessionProvider>
  );
}
