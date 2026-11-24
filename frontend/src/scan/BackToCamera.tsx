/**
 * Back to camera (Ali, 2026-11-24, inventory-tng-fdnt): while the camera
 * is open, a button at the bottom of the screen whenever its picture is
 * wholly or partly off the screen, which scrolls it back into view. Hidden
 * while a text box has focus, as the on-screen keyboard is then up. While
 * it may show, the page keeps room for it at its bottom, and a box given
 * focus lands above it (styles.css, .back-to-camera-room).
 */
import { msg } from "@lingui/core/macro";
import { useSignal } from "@preact/signals";
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

export function BackToCamera({ camera }: { camera: HTMLElement }) {
  const offScreen = useSignal(false);
  const typing = useSignal(isTextBox(document.activeElement));

  useEffect(() => {
    const root = document.documentElement;
    root.classList.add(ROOM);
    // Wholly in view, the picture is all inside the screen: a pixel's
    // margin, so a picture at a fraction of a pixel still counts.
    const watching = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          offScreen.value = entry.intersectionRatio < 1;
        }
      },
      { threshold: 1, rootMargin: "1px" },
    );
    watching.observe(camera);
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
      watching.disconnect();
      listening.abort();
    };
  }, [camera]);

  if (!offScreen.value || typing.value) {
    return null;
  }
  return (
    <button
      type="button"
      class="back-to-camera"
      onClick={() => {
        camera.scrollIntoView({ block: "start" });
        telemetry.record({ kind: "back_to_camera" });
      }}
    >
      {say(BACK_TO_CAMERA)}
    </button>
  );
}
