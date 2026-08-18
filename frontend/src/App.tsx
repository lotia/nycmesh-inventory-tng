import Alert from "@mui/material/Alert";
import Container from "@mui/material/Container";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

/**
 * Placeholder shell. The inventory UI -- including the multi-scan QR flow that
 * this project exists to provide -- is still being designed; see the DESIGN
 * issues in `bd list` and docs/architecture.md.
 */
export default function App() {
  return (
    <Container maxWidth="sm" sx={{ py: 6 }}>
      <Stack spacing={2}>
        <Typography variant="h4" component="h1">
          NYC Mesh Inventory
        </Typography>
        <Alert severity="info">Frontend scaffold. No inventory features are implemented yet.</Alert>
        <Typography variant="body2" color="text.secondary">
          See DEVELOPERS.md to get a local environment running.
        </Typography>
      </Stack>
    </Container>
  );
}
