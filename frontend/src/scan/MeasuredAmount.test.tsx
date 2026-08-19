/**
 * The question a measured item is never added without.
 *
 * Decision 0011 section 5: where the unit is not `each`, a scan opens this and
 * requires an entry. What matters is that no path through it ends with the
 * label's own number going in unlooked-at.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MeasuredAmount } from "./MeasuredAmount";

const cable = {
  label: {
    code: "4NP8R7T2WQ",
    item: { id: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre" },
    quantity: 305,
  },
  revoked: false,
};

function show(onEntered = vi.fn(), onCancel = vi.fn()) {
  render(<MeasuredAmount measured={cable} onCancel={onCancel} onEntered={onEntered} />);
  return { onEntered, onCancel };
}

const amount = () => screen.getByRole("textbox", { name: /amount in metre/i });
const add = () => screen.getByRole("button", { name: /^add$/i });

describe("saying how much", () => {
  it("asks about the item that was scanned, in its own unit", () => {
    show();
    expect(screen.getByRole("heading", { name: /how much cat6 outdoor/i })).toBeInTheDocument();
    expect(amount()).toBeInTheDocument();
  });

  it("starts empty, so nobody taps through the label's number", () => {
    show();
    expect(amount()).toHaveValue("");
    expect(add()).toBeDisabled();
  });

  it("says what a full one is, as a starting point and not as an answer", () => {
    show();
    expect(screen.getByText(/a full one is 305 metre/i)).toBeInTheDocument();
  });

  it("hands back what was typed", () => {
    const { onEntered } = show();
    fireEvent.change(amount(), { target: { value: "12.5" } });
    fireEvent.click(add());
    expect(onEntered).toHaveBeenCalledWith(12.5);
  });

  it("takes Enter, because a wedge scanner and a keypad both end that way", () => {
    const { onEntered } = show();
    fireEvent.change(amount(), { target: { value: "40" } });
    fireEvent.keyDown(amount(), { key: "Enter" });
    expect(onEntered).toHaveBeenCalledWith(40);
  });

  it("refuses an amount that is not one", () => {
    show();
    for (const nonsense of ["0", "-3", "abc", "  "]) {
      fireEvent.change(amount(), { target: { value: nonsense } });
      expect(add()).toBeDisabled();
    }
  });

  it("does not add anything when the scan is abandoned", () => {
    const { onCancel, onEntered } = show();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onEntered).not.toHaveBeenCalled();
  });
});
