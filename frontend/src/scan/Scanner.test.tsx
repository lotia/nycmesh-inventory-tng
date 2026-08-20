/**
 * Filling the batch by code, the way the two input paths that need no camera
 * are actually used: a scanner gun typing into whatever has focus, and a
 * volunteer reading the characters printed under a QR that will not scan.
 *
 * Both end in the same place as a camera decode, so what is asserted here --
 * that a code becomes a cart line, that an unknown one points at the
 * catalogue, that the box empties itself for the next code -- is asserted for
 * the camera too, up to the decode itself.
 */
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCart } from "../cart/CartProvider";
import { STORAGE_KEY } from "../cart/cartStorage";
import { cable, zipTies } from "../items/testFixtures";
import { callsTo, renderScreen } from "../testHarness";
import { Scanner } from "./Scanner";
import {
  CABLE_LABEL,
  clearMediaDevices,
  deferred,
  PACKET,
  refusing,
  serving,
  stubMediaDevices,
  videoInput,
} from "./testFixtures";

/**
 * The label read held open, so a test can put something else in the cart while
 * a code is still resolving.
 */
function servingSlowly(label: unknown): () => void {
  const held = deferred<void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.startsWith("/api/labels/")) {
        await held.promise;
        return new Response(JSON.stringify(label), { status: 200 });
      }
      return new Response(JSON.stringify(zipTies), { status: 200 });
    }),
  );
  return () => {
    held.resolve();
  };
}

function show() {
  return renderScreen(<Scanner />);
}

/** Somewhere else in the app writing to the cart: the item list's stepper. */
function Stepper() {
  const { dispatch } = useCart();
  return (
    <button
      type="button"
      onClick={() =>
        dispatch({
          type: "add",
          item: { id: 9, name: "Something Else", unitOfMeasure: "each" },
          quantity: 1,
        })
      }
    >
      Step
    </button>
  );
}

/** Type a code and press Enter, which is exactly what a scanner gun does. */
function wedge(code: string): void {
  const field = screen.getByRole("textbox", { name: /scan or type a code/i });
  fireEvent.change(field, { target: { value: code } });
  fireEvent.submit(screen.getByRole("form", { name: /scan a code/i }));
}

function stored(): { lines: { name: string; quantity: number }[] } {
  return JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
}

function vibration(): ReturnType<typeof vi.fn> {
  const vibrate = vi.fn();
  Object.defineProperty(navigator, "vibrate", { value: vibrate, configurable: true });
  return vibrate;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearMediaDevices();
  Reflect.deleteProperty(navigator, "vibrate");
});

describe("a code typed or sent by a scanner gun", () => {
  it("puts the item in the batch, in the amount the sticker stands for", async () => {
    serving(PACKET);
    show();
    wedge("7QK3M2XV9A");

    expect(await screen.findByRole("alert")).toHaveTextContent("Added 100 × Zip Ties Reusable");
    await waitFor(() => expect(stored().lines).toHaveLength(1));
    expect(stored().lines[0]).toMatchObject({ name: "Zip Ties Reusable", quantity: 100 });
  });

  it("empties the box, because the gun sends the next code straight after", async () => {
    serving(PACKET);
    show();
    wedge("7QK3M2XV9A");

    await screen.findByRole("alert");
    expect(screen.getByRole("textbox", { name: /scan or type a code/i })).toHaveValue("");
  });

  it("says so out loud, for somebody looking at the shelf and not the screen", async () => {
    const vibrate = vibration();
    serving(PACKET);
    show();
    wedge("7QK3M2XV9A");

    await screen.findByRole("alert");
    await waitFor(() => expect(vibrate).toHaveBeenCalled());
  });

  it("offers the catalogue for a code this system does not know", async () => {
    refusing({ detail: "Not found." }, 404);
    const vibrate = vibration();
    show();
    wedge("NOTACODE01");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Nothing here is labelled NOTACODE01\. Search for the item instead\./,
    );
    expect(stored().lines ?? []).toHaveLength(0);
    // Nothing reached the cart, so there is nothing to confirm by ear.
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("does not announce a cart edit no scan caused", async () => {
    // The confirmation is for a scan landing, and only for that. A code that
    // reached the cart and changed nothing -- the camera's second read of one
    // label, a wall code scanned twice -- must not leave the announcement
    // waiting for whatever the volunteer does next.
    const vibrate = vibration();
    const release = servingSlowly(PACKET);
    renderScreen(
      <>
        <Scanner />
        <Stepper />
      </>,
    );

    wedge("7QK3M2XV9A");
    fireEvent.click(screen.getByRole("button", { name: "Step" }));
    await waitFor(() => expect(stored().lines).toHaveLength(1));
    expect(vibrate).not.toHaveBeenCalled();

    release();
    await waitFor(() => expect(stored().lines).toHaveLength(2));
    expect(vibrate).toHaveBeenCalledTimes(1);
  });

  it("does nothing at all with an empty box", () => {
    serving(PACKET);
    show();
    wedge("   ");

    // The app reads who is signed in on load; what an empty box must not do is
    // resolve a code.
    expect(callsTo("/api/labels/")).toHaveLength(0);
  });
});

describe("the camera", () => {
  it("says why rather than hiding itself where the browser has no camera API", async () => {
    // The commonest cause is a phone reaching this over plain HTTP at a LAN
    // address, and that is exactly the setup where a button that is simply
    // absent leaves somebody with nothing to read and nothing to try. The
    // button is always offered; CameraScanner alone decides what it opens.
    serving(PACKET);
    show();
    fireEvent.click(screen.getByRole("button", { name: "Camera" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/served over HTTPS/);
  });

  it("opens and closes on the one button, so the lens is not left running", async () => {
    serving(PACKET);
    stubMediaDevices({
      getUserMedia: vi.fn(async () => {
        throw new DOMException("Denied", "NotAllowedError");
      }),
      enumerateDevices: async () => [videoInput("back", "Back Camera")],
    });
    show();

    fireEvent.click(screen.getByRole("button", { name: "Camera" }));
    expect(await screen.findByLabelText("Camera preview")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /stop camera/i }));
    expect(screen.queryByLabelText("Camera preview")).not.toBeInTheDocument();
  });

  it("asks how much before a measured item goes in the batch", async () => {
    // Decision 0011 section 5. The label says a full box is 305 m; what the
    // volunteer is holding is whatever is left of one.
    serving(CABLE_LABEL, cable);
    show();
    fireEvent.change(screen.getByRole("textbox", { name: /code/i }), {
      target: { value: "4NP8R7T2WQ" },
    });
    fireEvent.submit(screen.getByRole("form", { name: /scan a code/i }));

    const amount = await screen.findByRole("textbox", { name: /amount in metre/i });
    expect(stored().lines ?? []).toHaveLength(0);

    fireEvent.change(amount, { target: { value: "12.5" } });
    fireEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(stored().lines).toHaveLength(1));
    expect(stored().lines[0]).toMatchObject({ name: "Cat6 Outdoor", quantity: 12.5 });
  });
});

describe("a camera holding one label in frame", () => {
  it("asks about it once, not five times a second", async () => {
    // The reducer debounces repeat decodes, but a measured item deliberately
    // never reaches it -- it is being asked about first. Without the same
    // window ahead of resolution the keypad reopens under the finger.
    serving(CABLE_LABEL, cable);
    show();
    const field = screen.getByRole("textbox", { name: /code/i });

    for (let decode = 0; decode < 5; decode += 1) {
      fireEvent.change(field, { target: { value: "4NP8R7T2WQ" } });
      fireEvent.submit(screen.getByRole("form", { name: /scan a code/i }));
    }

    await screen.findByRole("textbox", { name: /amount in metre/i });
    const reads = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([path]) => typeof path === "string" && path.startsWith("/api/labels/"),
    );
    expect(reads).toHaveLength(1);
  });

  it("still takes a different code straight after", async () => {
    serving({ ...PACKET });
    show();
    const field = screen.getByRole("textbox", { name: /code/i });
    const form = screen.getByRole("form", { name: /scan a code/i });

    fireEvent.change(field, { target: { value: PACKET.code } });
    fireEvent.submit(form);
    await waitFor(() => expect(stored().lines).toHaveLength(1));

    fireEvent.change(field, { target: { value: "ZZZZZZZZZZ" } });
    fireEvent.submit(form);
    await waitFor(() =>
      expect(
        (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
          ([path]) => typeof path === "string" && path.startsWith("/api/labels/"),
        ),
      ).toHaveLength(2),
    );
  });
});
