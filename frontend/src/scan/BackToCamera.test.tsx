import { act, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";
import { stubInView } from "../testing/in-view";
import { BACK_TO_CAMERA, BackToCamera, ROOM } from "./BackToCamera";
import { useWhollyOnScreen } from "./camera-in-view";
import { useTextBoxFocused } from "./text-box-focus";

let view: ReturnType<typeof stubInView>;
let camera: HTMLVideoElement;
/** The camera's region, as Scanner draws it: focusable, the picture in it. */
let region: HTMLElement;

beforeEach(() => {
  view = stubInView();
  region = document.createElement("section");
  region.tabIndex = -1;
  camera = document.createElement("video");
  region.append(camera);
  document.body.append(region);
});

afterEach(() => {
  region.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const back = () => screen.queryByRole("button", { name: say(BACK_TO_CAMERA) });

/** The button, told what the camera's watch and the focus say, as
 * CartForm does. */
function Watched() {
  return (
    <BackToCamera
      camera={camera}
      onScreen={useWhollyOnScreen(camera)}
      typing={useTextBoxFocused()}
    />
  );
}

/** The page with a box of `type` and the button, the camera wholly in
 * view to begin with. */
function page(type: "text" | "checkbox" = "text") {
  const shown = render(
    <div>
      {type === "checkbox" ? (
        <input aria-label="box" type="checkbox" />
      ) : (
        <input aria-label="box" />
      )}
      <Watched />
    </div>,
  );
  view.inView(camera, 1);
  return { ...shown, box: screen.getByLabelText("box") };
}

describe("Back to camera", () => {
  test("shows only while the camera is wholly or partly off the screen", () => {
    page();
    expect(back()).toBeNull();
    view.inView(camera, 0.5);
    expect(back()).not.toBeNull();
    view.inView(camera, 0);
    expect(back()).not.toBeNull();
    view.inView(camera, 1);
    expect(back()).toBeNull();
  });

  test("hides while a text box has focus, and shows again once it leaves", () => {
    const { box } = page();
    view.inView(camera, 0);
    fireEvent.focusIn(box);
    expect(back()).toBeNull();
    fireEvent.focusOut(box, { relatedTarget: null });
    expect(back()).not.toBeNull();
  });

  test("stays while a box that takes no typing has focus", () => {
    const { box } = page("checkbox");
    view.inView(camera, 0);
    fireEvent.focusIn(box);
    expect(back()).not.toBeNull();
  });

  test("a tap scrolls the camera back into view, and is counted", () => {
    const scroll = vi.fn();
    camera.scrollIntoView = scroll;
    const record = vi.spyOn(telemetry, "record");
    page();
    view.inView(camera, 0.2);
    fireEvent.click(back() as Element);
    expect(scroll).toHaveBeenCalledWith({ block: "start" });
    expect(record).toHaveBeenCalledWith({ kind: "back_to_camera" });
  });

  test("a tap hands focus to the camera's region, as the button goes", () => {
    camera.scrollIntoView = vi.fn();
    page();
    view.inView(camera, 0);
    const button = back() as HTMLButtonElement;
    button.focus();
    fireEvent.click(button);
    view.inView(camera, 1);
    expect(back()).toBeNull();
    expect(document.activeElement).toBe(region);
  });

  test("the second tap of a double tap, on what scrolled under the finger, is ignored, focus back on the camera's region (inventory-tng-r3jx)", () => {
    camera.scrollIntoView = vi.fn();
    const { box } = page("checkbox");
    view.inView(camera, 0);
    fireEvent.click(back() as Element, { detail: 1 });
    view.inView(camera, 1);
    box.focus();
    expect(fireEvent.click(box, { detail: 1 })).toBe(false);
    expect(document.activeElement).toBe(region);
  });

  test("keeps room at the page's bottom while it may show, and none after", () => {
    const { unmount } = page();
    expect(document.documentElement.classList.contains(ROOM)).toBe(true);
    expect(view.watched()).toBe(1);
    act(unmount);
    expect(document.documentElement.classList.contains(ROOM)).toBe(false);
    expect(view.watched()).toBe(0);
  });
});
