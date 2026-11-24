/**
 * Whether a text box has focus, so the on-screen keyboard is up and the
 * volunteer is typing: Back to camera hides then (Ali, 2026-11-24,
 * inventory-tng-fdnt), and the camera reads no labels (Ali, 2026-11-24,
 * inventory-tng-5663).
 */
import { type ReadonlySignal, useSignal } from "@preact/signals";
import { useEffect } from "preact/hooks";

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

/** Whether a text box on the page has focus, followed while mounted. */
export function useTextBoxFocused(): ReadonlySignal<boolean> {
  const typing = useSignal(isTextBox(document.activeElement));
  useEffect(() => {
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
    return () => listening.abort();
  }, []);
  return typing;
}
