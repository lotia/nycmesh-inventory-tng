import { createTheme } from "@mui/material/styles";

// Central theme. Keep colour and typography decisions here rather than
// scattering sx overrides through components.
export const theme = createTheme({
  colorSchemes: { light: true, dark: true },
});
