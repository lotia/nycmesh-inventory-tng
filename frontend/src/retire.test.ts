import { afterEach, beforeEach, describe, expect, it } from "vitest";
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
