import { act, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";
import { stubInView } from "../testing/in-view";
import { BACK_TO_CAMERA, BackToCamera, isTextBox, ROOM } from "./BackToCamera";

let view: ReturnType<typeof stubInView>;
let camera: HTMLVideoElement;

beforeEach(() => {
  view = stubInView();
  camera = document.createElement("video");
  document.body.append(camera);
});

afterEach(() => {
  camera.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const back = () => screen.queryByRole("button", { name: say(BACK_TO_CAMERA) });

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
      <BackToCamera camera={camera} />
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

  test("keeps room at the page's bottom while it may show, and none after", () => {
    const { unmount } = page();
    expect(document.documentElement.classList.contains(ROOM)).toBe(true);
    expect(view.watched()).toBe(1);
    act(unmount);
    expect(document.documentElement.classList.contains(ROOM)).toBe(false);
    expect(view.watched()).toBe(0);
  });
});

describe("a text box", () => {
  test("is a box the volunteer types in, not a choice or a button", () => {
    const made = (tag: string, type?: string) => {
      const element = document.createElement(tag);
      if (type !== undefined) {
        element.setAttribute("type", type);
      }
      return element;
    };
    expect(isTextBox(made("input"))).toBe(true);
    expect(isTextBox(made("input", "search"))).toBe(true);
    expect(isTextBox(made("input", "number"))).toBe(true);
    expect(isTextBox(made("textarea"))).toBe(true);
    expect(isTextBox(made("input", "radio"))).toBe(false);
    expect(isTextBox(made("input", "checkbox"))).toBe(false);
    expect(isTextBox(made("select"))).toBe(false);
    expect(isTextBox(made("button"))).toBe(false);
    expect(isTextBox(null)).toBe(false);
  });
});
