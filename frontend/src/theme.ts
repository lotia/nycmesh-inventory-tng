import { createTheme, type ThemeOptions } from "@mui/material/styles";

// Central theme. Keep colour and typography decisions here rather than
// scattering sx overrides through components.
//
// The OPTIONS are exported as well as the theme, and that is not a
// convenience. Django's sign-in pages are styled from the same values --
// scripts/theme-css.ts resolves these into CSS custom properties and writes
// the stylesheet those pages load, so the two surfaces cannot drift. It reads
// this object rather than the theme above because generating the properties
// needs MUI's `cssVariables` mode, and turning that on here would change how
// the app itself emits styles for the sake of a file the app never loads.
export const themeOptions: ThemeOptions = {
  colorSchemes: { light: true, dark: true },
};

export const theme = createTheme(themeOptions);
