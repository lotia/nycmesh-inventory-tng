import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import { CartProvider } from "./cart/CartProvider";
import { ItemList } from "./items/ItemList";
import { VolunteerPicker } from "./volunteers/VolunteerPicker";

/**
 * The volunteer app.
 *
 * Two pieces so far: who the batch is attributed to, and the catalogue --
 * the path that works with no camera and no readable label. The scanner and
 * the submit bar are the rest of the flow designed in
 * docs/decisions/0011-qr-batch-scanning.md; what is built and what is not is
 * listed in docs/architecture.md.
 *
 * The cart provider is here rather than at the root so that a test renders the
 * app and gets the app, batch state included.
 */
export default function App() {
  return (
    <CartProvider>
      <Container maxWidth="sm" sx={{ py: 4 }}>
        <Stack spacing={3}>
          <Typography variant="h4" component="h1">
            NYC Mesh Inventory
          </Typography>
          <VolunteerPicker />
          <ItemList />
        </Stack>
      </Container>
    </CartProvider>
  );
}
