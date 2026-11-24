import { act, fireEvent, render } from "@testing-library/preact";
import { describe, expect, test } from "vitest";
import { isTextBox, useTextBoxFocused } from "./text-box-focus";

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

describe("whether a text box has focus", () => {
  test("follows focus coming into and leaving text boxes", () => {
    const said: boolean[] = [];
    function Watcher() {
      said.push(useTextBoxFocused().value);
      return (
        <div>
          <input aria-label="box" />
          <input aria-label="tick" type="checkbox" />
        </div>
      );
    }
    const { getByLabelText, unmount } = render(<Watcher />);
    const [box, tick] = [getByLabelText("box"), getByLabelText("tick")];
    expect(said.at(-1)).toBe(false);
    fireEvent.focusIn(box);
    expect(said.at(-1)).toBe(true);
    // Straight to a box that takes no typing.
    fireEvent.focusOut(box, { relatedTarget: tick });
    expect(said.at(-1)).toBe(false);
    fireEvent.focusOut(tick, { relatedTarget: box });
    expect(said.at(-1)).toBe(true);
    fireEvent.focusOut(box, { relatedTarget: null });
    expect(said.at(-1)).toBe(false);
    act(unmount);
  });
});
