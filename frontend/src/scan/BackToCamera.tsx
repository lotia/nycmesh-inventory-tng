/**
 * Back to camera (Ali, 2026-11-24, inventory-tng-fdnt): while the camera
 * is open, a button at the bottom of the screen whenever its picture is
 * wholly or partly off the screen, which scrolls it back into view and
 * hands focus to the camera's region. Hidden while a text box has focus,
 * as the on-screen keyboard is then up (text-box-focus.ts). While it may
 * show, the page keeps room for it at its bottom, and a box given focus
 * lands above it (styles.css, .back-to-camera-room). Whether the picture
 * is on the screen comes from camera-in-view.ts, which the camera reads
 * too.
 */
import { msg } from "@lingui/core/macro";
import type { ReadonlySignal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { say } from "../shared/i18n";
import { focusInPlace, ignoreTaps } from "../shared/settling";
import { telemetry } from "../shared/telemetry";

export const BACK_TO_CAMERA = msg`Back to camera`;

/** The class on the page's root while the button may show. */
export const ROOM = "back-to-camera-room";

export function BackToCamera({
  camera,
  onScreen,
  typing,
}: {
  camera: HTMLElement;
  /** Whether the camera's picture is wholly on the screen
   * (useWhollyOnScreen). */
  onScreen: ReadonlySignal<boolean>;
  /** Whether a text box has focus (useTextBoxFocused). */
  typing: ReadonlySignal<boolean>;
}) {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add(ROOM);
    return () => root.classList.remove(ROOM);
  }, []);

  if (onScreen.value || typing.value) {
    return null;
  }
  return (
    <button
      type="button"
      class="back-to-camera"
      onClick={() => {
        camera.scrollIntoView({ block: "start" });
        // The button goes once the picture shows: focus goes to the
        // camera's region (Scanner), not lost to the page, and taps wait
        // a moment, as everything moved under the finger
        // (inventory-tng-r3jx).
        const region = () => camera.closest("section");
        focusInPlace(region());
        ignoreTaps(document, region);
        telemetry.record({ kind: "back_to_camera" });
      }}
    >
      {say(BACK_TO_CAMERA)}
    </button>
  );
}
