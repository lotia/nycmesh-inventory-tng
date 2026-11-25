/** The scan page's boxes in the decimal separator its server chose from
 * the browser's Accept-Language (web/decimal_separator.py): read in it,
 * the other refused naming it, and each such refusal reported as Record
 * is tapped (inventory-tng-qnwz). */
import { fireEvent, screen } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { takeSeparatorFrom } from "../shared/quantity";
import { telemetry } from "../shared/telemetry";
import { fillMove, open, ready, save, saveQueued } from "../testing/scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

beforeEach(() => {
  document.documentElement.dataset.decimal = ",";
  takeSeparatorFrom(document.documentElement);
});

afterEach(() => {
  delete document.documentElement.dataset.decimal;
});

test("a point in a comma page is refused, naming the comma, and reported", async () => {
  const record = vi.spyOn(telemetry, "record");
  record.mockClear();
  const outbox = open();
  await ready();
  fillMove();
  const loose = screen.getByLabelText("Loose m");
  fireEvent.input(loose, { target: { value: "1.5" } });
  save();
  // Beside the box as typed, and among what Record says is to do.
  await vi.waitFor(() =>
    expect(
      screen
        .getAllByText(/Use “,” for decimals/)
        .map((said) => said.closest("li")?.textContent),
    ).toContain("Cat6 cable: Use “,” for decimals, such as 0,5."),
  );
  expect(
    record.mock.calls.filter(([event]) => event.kind === "other_separator"),
  ).toEqual([[{ kind: "other_separator", separator: "point" }]]);
  fireEvent.input(loose, { target: { value: "1,5" } });
  await saveQueued(outbox, 1);
  expect(outbox.entries.value[0]?.body.lines).toMatchObject([
    { quantity: "1.5" },
  ]);
});

test("an amount in words on a comma page takes its comma, ungrouped", async () => {
  // Ali, 2026-11-23 (inventory-tng-4mc3).
  open();
  await ready();
  fillMove();
  const loose = screen.getByLabelText("Loose m");
  fireEvent.input(loose, { target: { value: "1500" } });
  expect(await screen.findByText("1500 m in all")).not.toBeNull();
  fireEvent.input(loose, { target: { value: "1,5" } });
  expect(await screen.findByText("1,5 m in all")).not.toBeNull();
  fireEvent.input(loose, { target: { value: "1234,5" } });
  expect(await screen.findByText("1234,5 m in all")).not.toBeNull();
});

test("on a point page an amount in words is ungrouped too", async () => {
  // Ali, 2026-11-24 (inventory-tng-ua04): as the page's boxes take it.
  delete document.documentElement.dataset.decimal;
  takeSeparatorFrom(document.documentElement);
  open();
  await ready();
  fillMove();
  const loose = screen.getByLabelText("Loose m");
  fireEvent.input(loose, { target: { value: "1234.5" } });
  expect(await screen.findByText("1234.5 m in all")).not.toBeNull();
});
