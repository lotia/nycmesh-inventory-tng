/**
 * Back to camera (Ali, 2026-11-24, inventory-tng-fdnt): while the camera
 * is open, a button at the bottom of the screen whenever its picture is
 * wholly or partly off the screen, which scrolls it back into view and
 * hands focus to the camera's region. Hidden while a text box has focus,
 * as the on-screen keyboard is then up. While it may show, the page keeps
 * room for it at its bottom, and a box given focus lands above it
 * (styles.css, .back-to-camera-room). Whether the picture is on the
 * screen comes from camera-in-view.ts, which the camera reads too.
 */
import { msg } from "@lingui/core/macro";
import { type ReadonlySignal, useSignal } from "@preact/signals";
import { useEffect } from "preact/hooks";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";

export const BACK_TO_CAMERA = msg`Back to camera`;

/** The class on the page's root while the button may show. */
export const ROOM = "back-to-camera-room";

/** Inputs that take no typing, so open no on-screen keyboard. */
const UNTYPED = new Set([
  "button",
  "checkbox",
  "color",
  "file",
  "hidden",
  "image",
  "radio",
  "range",
  "reset",
  "submit",
]);

/** Whether `target` is a box the volunteer types in. */
export function isTextBox(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLInputElement && !UNTYPED.has(target.type))
  );
}

export function BackToCamera({
  camera,
  onScreen,
}: {
  camera: HTMLElement;
  /** Whether the camera's picture is wholly on the screen
   * (useWhollyOnScreen). */
  onScreen: ReadonlySignal<boolean>;
}) {
  const typing = useSignal(isTextBox(document.activeElement));

  useEffect(() => {
    const root = document.documentElement;
    root.classList.add(ROOM);
    const listening = new AbortController();
    const { signal } = listening;
    document.addEventListener(
      "focusin",
      (event) => {
        typing.value = isTextBox(event.target);
      },
      { signal },
    );
    // Where focus goes next, if anywhere.
    document.addEventListener(
      "focusout",
      (event) => {
        typing.value = isTextBox(event.relatedTarget);
      },
      { signal },
    );
    return () => {
      root.classList.remove(ROOM);
      listening.abort();
    };
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
        // camera's region (Scanner), not lost to the page.
        camera.closest("section")?.focus({ preventScroll: true });
        telemetry.record({ kind: "back_to_camera" });
      }}
    >
      {say(BACK_TO_CAMERA)}
    </button>
  );
}
