import { act, render } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { stubInView } from "../testing/in-view";
import { scrollToPicture, useWhollyOnScreen } from "./camera-in-view";

let view: ReturnType<typeof stubInView>;
let picture: HTMLVideoElement;

beforeEach(() => {
  view = stubInView();
  picture = document.createElement("video");
  document.body.append(picture);
});

afterEach(() => {
  picture.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** What the watch on `watched` says, each time it says it. */
function watching(watched: Element | null) {
  const said: boolean[] = [];
  function Watcher() {
    said.push(useWhollyOnScreen(watched).value);
    return null;
  }
  const { unmount } = render(<Watcher />);
  return { last: () => said.at(-1), unmount };
}

describe("whether the picture is wholly on the screen", () => {
  test("is so until the browser says otherwise, and only while all of it shows", () => {
    const { last } = watching(picture);
    expect(last()).toBe(true);
    view.inView(picture, 0.99);
    expect(last()).toBe(false);
    view.inView(picture, 0);
    expect(last()).toBe(false);
    view.inView(picture, 1);
    expect(last()).toBe(true);
  });

  test("is not watched with no picture, nor once the picture goes", () => {
    watching(null);
    expect(view.watched()).toBe(0);
    const { unmount } = watching(picture);
    expect(view.watched()).toBe(1);
    act(unmount);
    expect(view.watched()).toBe(0);
  });
});

describe("scrolling to the picture", () => {
  function prefers(reduced: boolean) {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query) =>
        ({
          matches: reduced && query === "(prefers-reduced-motion: reduce)",
        }) as MediaQueryList,
    );
  }

  test("is smooth", () => {
    prefers(false);
    const scroll = vi.fn();
    picture.scrollIntoView = scroll;
    scrollToPicture(picture);
    expect(scroll).toHaveBeenCalledWith({ block: "start", behavior: "smooth" });
  });

  test("jumps for one who asked for less motion", () => {
    prefers(true);
    const scroll = vi.fn();
    picture.scrollIntoView = scroll;
    scrollToPicture(picture);
    expect(scroll).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
  });
});
