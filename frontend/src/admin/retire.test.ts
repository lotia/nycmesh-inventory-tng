import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { guardRetire, unsaved } from "./retire";

guardRetire(document);

/** Each form sent, and whether the guard stopped it; none is sent on. */
let sent: { form: string; stopped: boolean }[] = [];
function record(event: Event) {
  sent.push({
    form: (event.target as HTMLFormElement).id,
    stopped: event.defaultPrevented,
  });
  event.preventDefault();
}

beforeEach(() => {
  sent = [];
  window.addEventListener("submit", record);
  document.body.innerHTML = `
    <form id="save">
      <input name="name" value="Cable">
      <input type="hidden" name="version" value="3">
      <input type="checkbox" name="fragile">
      <select name="unit"><option selected>each</option><option>m</option></select>
      <select name="kind"><option>store</option><option>van</option></select>
      <textarea name="notes">Grey</textarea>
      <button>Save</button>
    </form>
    <form id="retire" data-retire><button>Retire</button></form>`;
});

afterEach(() => {
  window.removeEventListener("submit", record);
  document.body.replaceChildren();
});

function form(id: string): HTMLFormElement {
  return document.getElementById(id) as HTMLFormElement;
}

function field(name: string) {
  return form("save").elements.namedItem(name) as
    | HTMLInputElement
    | HTMLSelectElement
    | HTMLTextAreaElement;
}

describe("unsaved", () => {
  it("is false for a form as it was sent", () => {
    expect(unsaved(form("save"))).toBe(false);
  });

  it.each([
    ["typed in", () => (field("name").value = "Cable, grey")],
    ["ticked", () => ((field("fragile") as HTMLInputElement).checked = true)],
    ["picked", () => (field("unit").value = "m")],
    [
      "picked from a list sent with none marked",
      () => (field("kind").value = "van"),
    ],
    ["noted", () => (field("notes").value = "Black")],
  ])("is true once %s", (_how, change) => {
    change();
    expect(unsaved(form("save"))).toBe(true);
  });

  it("ignores a hidden field the page changed", () => {
    field("version").value = "4";
    expect(unsaved(form("save"))).toBe(false);
  });
});

describe("retiring", () => {
  it("goes ahead at once with nothing unsaved", () => {
    form("retire").requestSubmit();
    expect(sent).toEqual([{ form: "retire", stopped: false }]);
    expect(document.querySelector("dialog")).toBeNull();
  });

  it("asks first with something unsaved, and goes back when told", () => {
    field("name").value = "Cable, grey";
    form("retire").requestSubmit();
    expect(sent).toEqual([{ form: "retire", stopped: true }]);
    const dialog = document.querySelector("dialog");
    expect(dialog?.open).toBe(true);
    expect(dialog?.textContent).toContain("changes you haven't");
    dialog?.close("back");
    expect(sent).toHaveLength(1);
    expect(document.querySelector("dialog")).toBeNull();
    expect(field("name").value).toBe("Cable, grey");
  });

  it("closed, ignores taps a moment, and puts focus back on Retire, without scrolling (Ali, 2026-11-27, inventory-tng-r3jx)", () => {
    field("name").value = "Cable, grey";
    const retire = form("retire").querySelector("button") as HTMLElement;
    form("retire").requestSubmit(retire);
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    document.querySelector("dialog")?.close("back");
    expect(document.activeElement).toBe(retire);
    expect(focus.mock.calls).toEqual([[{ preventScroll: true }]]);
    focus.mockRestore();
    // The second tap of a double tap, on Save, under where Go back was.
    const save = form("save").querySelector("button") as HTMLElement;
    save.focus();
    expect(
      save.dispatchEvent(
        new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }),
      ),
    ).toBe(false);
    expect(sent).toHaveLength(1);
    expect(document.activeElement).toBe(retire);
  });

  it("retires anyway when told", () => {
    field("unit").value = "m";
    form("retire").requestSubmit();
    document.querySelector("dialog")?.close("retire");
    expect(sent).toEqual([
      { form: "retire", stopped: true },
      { form: "retire", stopped: false },
    ]);
  });

  it("leaves other forms alone", () => {
    field("name").value = "Cable, grey";
    form("save").requestSubmit();
    expect(sent).toEqual([{ form: "save", stopped: false }]);
  });
});
