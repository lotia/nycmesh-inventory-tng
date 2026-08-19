import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { SessionProvider } from "./admin/SessionProvider";
import { StaleSession } from "./admin/StepUp";
import { SubmitBar } from "./batch/SubmitBar";
import { CartProvider } from "./cart/CartProvider";
import { ItemList } from "./items/ItemList";
import { DeepLink } from "./scan/DeepLink";
import { Scanner } from "./scan/Scanner";
import { VolunteerPicker } from "./volunteers/VolunteerPicker";

/**
 * The volunteer app.
 *
 * The whole flow designed in docs/decisions/0011-qr-batch-scanning.md: the
 * code a label's QR opened the app with, who the batch is attributed to, the
 * three ways to fill it -- scanned, wedged or typed above, browsed in the
 * catalogue below, which is the path that works with no camera and no readable
 * label -- and the Save that sends it. What is built and what is not is listed
 * in docs/architecture.md.
 *
 * Both providers are here rather than at the root so that a test renders the
 * app and gets the app -- the batch in hand, and who is signed in, which is
 * what decides whether the administrative controls are drawn at all.
 */
export default function App() {
  return (
    <SessionProvider>
      <CartProvider>
        <Container maxWidth="sm" sx={{ py: 4 }}>
          <Stack spacing={3}>
            <Typography variant="h4" component="h1">
              NYC Mesh Inventory
            </Typography>
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
        </Container>
      </CartProvider>
    </SessionProvider>
  );
}
