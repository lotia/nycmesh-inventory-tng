/**
 * The OpenTelemetry SDK itself, in a module nothing imports statically.
 *
 * SEPARATE FROM `start.ts` FOR ONE REASON, and it is a runtime cost rather
 * than a bundle size. `@opentelemetry/context-zone` is a re-export plus
 * `import 'zone.js'`, and the package declares `sideEffects: true`, so no
 * bundler may drop it: importing it anywhere in the entry chunk replaces
 * `window.Promise` with `ZoneAwarePromise` and patches `setTimeout`,
 * `requestAnimationFrame`, `XMLHttpRequest` and
 * `EventTarget.prototype.addEventListener` for the life of the page. Every
 * async operation in React, in MUI and in the five-a-second decode loop would
 * then run through Zone's wrappers on the phone `start.ts` promises pays for
 * none of it. Reached only through `await import()`, behind the token check,
 * that promise is kept.
 *
 * PROPAGATION IS THE POINT. `instrumentation-fetch` puts `traceparent` on
 * every call to `/api`, nginx forwards it untouched, and the backend's sampler
 * reads it. `/api` is same-origin behind that nginx, so there is no CORS and
 * no `propagateTraceHeaderCorsUrls` to configure -- that knob becomes
 * necessary only if the API ever moves to another host.
 *
 * THE POLICY IS NOT WIDENED. `connect-src 'self'` stays exactly as it is
 * (decision 0021, and `frontend/nginx.conf.template` is where it is written),
 * so the exporter posts to a path on this origin and nginx forwards it to the
 * collector. No preflight, and the collector is never internet-facing.
 */

import { ZoneContextManager } from "@opentelemetry/context-zone";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor, WebTracerProvider } from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

import { wiring } from "./wiring";

/** What these spans are filed under, beside the backend's own service. */
export const SERVICE = "inventory-tng-frontend";

/**
 * Build the SDK, register it, and hand back the way to take it down again.
 *
 * The teardown is returned rather than left implicit because there is a button
 * on screen that has to work: `Recording.tsx` says why stopping is a promise
 * this application makes rather than a convenience. Undoing the
 * instrumentations as well as shutting the provider down, because they are
 * patched onto the global `fetch` and would otherwise keep making spans for a
 * provider that is gone.
 */
export async function begin(token: string): Promise<() => Promise<void>> {
  const provider = new WebTracerProvider({
    resource: resourceFromAttributes({ [ATTR_SERVICE_NAME]: SERVICE }),
    spanProcessors: [new BatchSpanProcessor(new OTLPTraceExporter(wiring(token)))],
  });
  provider.register({
    // Without a context manager, a span opened before an `await` is not the
    // current span after it, and every fetch this app makes is behind one.
    contextManager: new ZoneContextManager(),
  });
  const disable = registerInstrumentations({
    instrumentations: [
      new DocumentLoadInstrumentation(),
      // The header that makes the backend record these requests in full is
      // added by `api/client.ts`, which is the one place this app talks to
      // the API. This instrumentation's job is the `traceparent` that ties
      // the two ends of one click together.
      new FetchInstrumentation(),
    ],
  });
  return async () => {
    disable();
    // Flushes what is queued on the way down, so the last few spans of the
    // session somebody is debugging are the ones they get to look at.
    await provider.shutdown();
  };
}
