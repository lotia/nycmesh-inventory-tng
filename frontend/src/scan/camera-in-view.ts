/**
 * Whether the camera's picture is wholly on the screen, and bringing it
 * there as the camera opens (Ali, 2026-11-24, inventory-tng-rj2g). One
 * watch serves both the camera, which reads no labels while its picture
 * is not wholly on the screen, and Back to camera, which shows then.
 */
import { type ReadonlySignal, useSignal } from "@preact/signals";
import { useEffect } from "preact/hooks";

/** Whether `picture` is wholly on the screen: true until the browser says
 * otherwise, and while there is no picture. */
export function useWhollyOnScreen(
  picture: Element | null,
): ReadonlySignal<boolean> {
  const onScreen = useSignal(true);
  useEffect(() => {
    if (picture === null) {
      return;
    }
    // A pixel's margin, so a picture at a fraction of a pixel still
    // counts as wholly on the screen.
    const watching = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          onScreen.value = entry.intersectionRatio >= 1;
        }
      },
      { threshold: 1, rootMargin: "1px" },
    );
    watching.observe(picture);
    return () => {
      watching.disconnect();
      onScreen.value = true;
    };
  }, [picture]);
  return onScreen;
}

/** Scrolls `picture` to the screen's top, smoothly unless the volunteer
 * asked for less motion. */
export function scrollToPicture(picture: Element) {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  picture.scrollIntoView({
    block: "start",
    behavior: still ? "auto" : "smooth",
  });
}
