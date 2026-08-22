import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CartProvider, useCart } from "./CartProvider";
import { STORAGE_KEY } from "./cartStorage";
import { packet } from "./testFixtures";

/** What was on the device the instant `handOver` returned. */
let atHandOver: string | null = null;

function Harness() {
  const { cart, dispatch, handOver } = useCart();
  return (
    <>
      <button type="button" onClick={() => dispatch({ type: "scan", label: packet })}>
        Scan zip ties
      </button>
      <button
        type="button"
        onClick={() => {
          handOver();
          // Read here rather than after the click: what is being asserted is
          // that nothing -- a reload above all -- can land between the caller
          // storing the batch elsewhere and this cart being emptied.
          atHandOver = window.localStorage.getItem(STORAGE_KEY);
        }}
      >
        Hand it over
      </button>
      <button type="button" onClick={() => dispatch({ type: "setActor", actorId: 4 })}>
        Pick volunteer
      </button>
      <button type="button" onClick={() => dispatch({ type: "setKind", kind: "checkin" })}>
        Check in
      </button>
      <button type="button" onClick={() => dispatch({ type: "clear" })}>
        Start a new cart
      </button>
      <output aria-label="kind">{cart.kind}</output>
      <output aria-label="cart lines">
        {cart.lines.map((line) => `${line.name} x ${line.quantity}`).join(", ")}
      </output>
      <output aria-label="idempotency key">{cart.idempotencyKey}</output>
    </>
  );
}

function lines(): string {
  return screen.getByRole("status", { name: "cart lines" }).textContent ?? "";
}

function key(): string {
  return screen.getByRole("status", { name: "idempotency key" }).textContent ?? "";
}

function scanZipTies(): void {
  fireEvent.click(screen.getByRole("button", { name: "Scan zip ties" }));
}

beforeEach(() => {
  window.localStorage.clear();
  atHandOver = null;
});

// Restored here rather than at the end of the test that installs a spy: a
// failed assertion would otherwise skip the restore and leave the stub in
// place for everything that runs after it.
afterEach(() => {
  vi.restoreAllMocks();
});

describe("CartProvider", () => {
  it("keeps the cart when the page is reloaded", () => {
    const first = render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );
    scanZipTies();
    expect(lines()).toBe("Zip Ties Reusable x 100");
    first.unmount();

    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );

    expect(lines()).toBe("Zip Ties Reusable x 100");
  });

  it("mints a fresh idempotency key when the volunteer changes", () => {
    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );
    const opened = key();

    fireEvent.click(screen.getByRole("button", { name: "Pick volunteer" }));

    expect(key()).not.toBe(opened);
    expect(key()).toMatch(/^[0-9a-f]{32}$/);
  });

  it("carries the batch's own fields without disturbing its key", () => {
    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );
    const opened = key();

    fireEvent.click(screen.getByRole("button", { name: "Check in" }));

    expect(screen.getByRole("status", { name: "kind" })).toHaveTextContent("checkin");
    expect(key()).toBe(opened);
  });

  it("starts the next batch under its own key once the cart is cleared", () => {
    // The provider's own job, not the reducer's: clearing mints a fresh key
    // and a fresh creation time, which a pure reducer cannot invent.
    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );
    scanZipTies();
    const first = key();

    fireEvent.click(screen.getByRole("button", { name: "Start a new cart" }));

    expect(key()).not.toBe(first);
    expect(lines()).toBe("");
  });

  it("puts the emptied cart on the device before handing back, not on the next render", () => {
    // The submit bar calls this once the queue is holding the batch; what a
    // reload in between would do to it is on `handOver` itself.
    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );
    scanZipTies();
    const handedOver = key();

    fireEvent.click(screen.getByRole("button", { name: "Hand it over" }));

    const written = JSON.parse(atHandOver ?? "null");
    expect(written.lines).toEqual([]);
    expect(written.idempotencyKey).not.toBe(handedOver);
    // And the screen agrees with what was stored: one key, not two.
    expect(written.idempotencyKey).toBe(key());
  });

  it("is unusable outside a provider rather than silently starting a second cart", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    expect(() => render(<Harness />)).toThrow(/CartProvider/);
  });
});

describe("CartProvider scan debounce", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts one packet however many times the camera decodes it", () => {
    render(
      <CartProvider>
        <Harness />
      </CartProvider>,
    );

    scanZipTies();
    scanZipTies();
    scanZipTies();

    expect(lines()).toBe("Zip Ties Reusable x 100");
  });
});
