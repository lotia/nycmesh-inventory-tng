import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { copyButtons, printButtons } from "./buttons";

beforeEach(() => {
  document.body.innerHTML = `
    <p id="link">https://inventory.example/admin/reset?token=t</p>
    <button type="button" data-print hidden>Print</button>
    <button type="button" data-copy="link" data-status="said"
      data-copied="Copied." data-failed="Couldn't copy." hidden>Copy</button>
    <span id="said"></span>`;
});

afterEach(() => {
  document.body.replaceChildren();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const button = (selector: string) =>
  document.querySelector(selector) as HTMLButtonElement;
const said = () => document.getElementById("said")?.textContent;

test("a print button shows, and prints the page", () => {
  // happy-dom has no print().
  const print = vi.fn();
  vi.stubGlobal("print", print);
  printButtons(document);
  expect(button("[data-print]").hidden).toBe(false);
  button("[data-print]").click();
  expect(print).toHaveBeenCalledOnce();
});

test("a copy button copies its text and says so", async () => {
  const writeText = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue(undefined);
  copyButtons(document);
  expect(button("[data-copy]").hidden).toBe(false);
  button("[data-copy]").click();
  expect(writeText).toHaveBeenCalledWith(
    "https://inventory.example/admin/reset?token=t",
  );
  await vi.waitFor(() => expect(said()).toBe("Copied."));
});

test("a copy that fails says so", async () => {
  vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("no"));
  copyButtons(document);
  button("[data-copy]").click();
  await vi.waitFor(() => expect(said()).toBe("Couldn't copy."));
});

test("a copy button naming nothing copies nothing, and says nothing", () => {
  document.body.innerHTML = `<button type="button" data-copy hidden>Copy</button>`;
  const writeText = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue(undefined);
  copyButtons(document);
  button("[data-copy]").click();
  expect(writeText).toHaveBeenCalledWith("");
});

test("without a clipboard, there is no copy button", () => {
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue(
    undefined as unknown as Clipboard,
  );
  copyButtons(document);
  expect(button("[data-copy]").hidden).toBe(true);
});
