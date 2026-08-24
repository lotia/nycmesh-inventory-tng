import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider } from "@mui/material/styles";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { watch } from "./telemetry/errors";
import { settle } from "./telemetry/flag";
import { Recording } from "./telemetry/Recording";
import { start } from "./telemetry/start";
import { theme } from "./theme";

// UNCONDITIONALLY, and before anything renders. A failure during the first
// render is reported rather than lost, and it is reported from every device
// rather than only from one an administrator handed a debug link to -- which
// is what `telemetry/report.ts` promises and what installing this behind the
// token check quietly took back. A phone in a basement is the one nobody will
// otherwise ever hear from.
watch();

// Spans are the other half, and they are not free, so they wait for somebody
// to have asked. `telemetry/flag.ts` says what an administrator's link is;
// `telemetry/start.ts` says why nothing is awaited here.
// Not held here and not passed anywhere: `start.ts` owns whether this device
// is recording, and the badge subscribes to it. A boolean computed here was a
// second answer to the same question, which is how it came to disagree.
void start(settle());

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root element #root not found in index.html");
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
      <Recording />
    </ThemeProvider>
  </StrictMode>,
);
