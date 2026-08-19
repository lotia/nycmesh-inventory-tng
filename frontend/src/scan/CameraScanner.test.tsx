/**
 * What can be asserted about the camera in a browser that has none.
 *
 * Everything up to the point a stream is granted: which cameras are offered,
 * that picking one is remembered and asked for exactly, and that each way of
 * being refused says what to do instead. Past that point -- a live stream,
 * frames, a decode -- there is nothing here to test against and nothing is
 * pretended. See the header of CameraScanner.tsx.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraScanner, refusal } from "./CameraScanner";
import { STORAGE_KEY } from "./cameras";
import { clearMediaDevices, stubMediaDevices, videoInput } from "./testFixtures";

/** A device with cameras that will not open, which is every device in jsdom. */
function refusing(...devices: MediaDeviceInfo[]): ReturnType<typeof vi.fn> {
  const getUserMedia = vi.fn(async () => {
    throw new DOMException("Denied", "NotAllowedError");
  });
  stubMediaDevices({ getUserMedia, enumerateDevices: async () => devices });
  return getUserMedia;
}

function open() {
  return render(<CameraScanner onCode={vi.fn()} />);
}

/** Pick a camera from the list, the way somebody taps it. */
function pick(label: string): void {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: /camera/i }));
  fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: label }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  clearMediaDevices();
});

describe("opening the camera", () => {
  it("says what to do instead where there is no camera API at all", async () => {
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(/served over HTTPS/);
  });

  it("says what to do instead when permission is refused", async () => {
    refusing(videoInput("back", "Back Camera"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /not letting the page use the camera/,
    );
  });

  it("lets go of a stream that arrived after the scanner was closed", async () => {
    // The volunteer taps Stop, or switches lens, while the permission prompt
    // is still up. The cleanup can only stop what exists when it runs, so a
    // stream granted after it has to let go of itself -- otherwise the phone
    // keeps the lens powered and the tab keeps its recording indicator lit.
    const stop = vi.fn();
    let grant: (stream: MediaStream) => void = () => undefined;
    const granted = new Promise<MediaStream>((resolve) => {
      grant = resolve;
    });
    stubMediaDevices({
      getUserMedia: vi.fn(() => granted),
      enumerateDevices: async () => [videoInput("back", "Back Camera")],
    });

    const { unmount } = open();
    unmount();
    grant({ getTracks: () => [{ stop }] } as unknown as MediaStream);

    await waitFor(() => expect(stop).toHaveBeenCalled());
  });

  it("asks for the back camera when nobody has chosen one", async () => {
    const getUserMedia = refusing(videoInput("back", "Back Camera"));
    open();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    expect(getUserMedia).toHaveBeenCalledWith({ video: { facingMode: "environment" } });
  });
});

describe("choosing the lens", () => {
  it("offers every camera the device reports", async () => {
    refusing(videoInput("back", "Back Camera"), videoInput("wide", "Back Ultra Wide Camera"));
    open();

    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /camera/i }));
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Back Camera",
      "Back Ultra Wide Camera",
    ]);
  });

  it("is not offered where there is only one camera to choose", async () => {
    refusing(videoInput("back", "Back Camera"));
    open();
    await screen.findByRole("alert");
    expect(screen.queryByRole("combobox", { name: /camera/i })).not.toBeInTheDocument();
  });

  it("asks for the chosen one exactly, so nothing can substitute the ultra-wide", async () => {
    const getUserMedia = refusing(
      videoInput("wide", "Back Ultra Wide Camera"),
      videoInput("back", "Back Camera"),
    );
    open();
    await screen.findByRole("combobox", { name: /camera/i });

    pick("Back Camera");
    await waitFor(() =>
      expect(getUserMedia).toHaveBeenLastCalledWith({ video: { deviceId: { exact: "back" } } }),
    );
  });

  it("remembers it, so a phone that answers badly costs one tap ever", async () => {
    refusing(videoInput("wide", "Back Ultra Wide Camera"), videoInput("back", "Back Camera"));
    open();
    await screen.findByRole("combobox", { name: /camera/i });

    pick("Back Camera");
    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBe('"back"'));
  });

  it("opens on the remembered one next time", async () => {
    window.localStorage.setItem(STORAGE_KEY, '"back"');
    const getUserMedia = refusing(videoInput("back", "Back Camera"));
    open();
    await waitFor(() =>
      expect(getUserMedia).toHaveBeenCalledWith({ video: { deviceId: { exact: "back" } } }),
    );
  });

  it("forgets a remembered camera the device no longer has", async () => {
    // Otherwise the choice is a dead end: the picker is only offered where the
    // device reports more than one camera, and a browser that mints new device
    // ids per session reports one until permission has been granted.
    window.localStorage.setItem(STORAGE_KEY, '"gone"');
    const getUserMedia = vi.fn(async () => {
      throw new DOMException("Not there", "OverconstrainedError");
    });
    stubMediaDevices({ getUserMedia, enumerateDevices: async () => [videoInput("back")] });
    open();

    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBe("null"));
    await waitFor(() =>
      expect(getUserMedia).toHaveBeenLastCalledWith({ video: { facingMode: "environment" } }),
    );
  });

  it("carries on without a picker if the device will not say what it has", async () => {
    stubMediaDevices({
      getUserMedia: vi.fn(async () => {
        throw new DOMException("Denied", "NotAllowedError");
      }),
      enumerateDevices: async () => {
        throw new Error("no");
      },
    });
    open();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /camera/i })).not.toBeInTheDocument();
  });
});

describe("why the camera did not open", () => {
  it("names the way past a camera that is gone or a device id that no longer exists", () => {
    expect(refusal(new DOMException("", "NotFoundError"))).toMatch(/Pick another one/);
    expect(refusal(new DOMException("", "OverconstrainedError"))).toMatch(/Pick another one/);
  });

  it("passes anything else on rather than inventing a reason", () => {
    expect(refusal(new Error("camera in use"))).toBe("camera in use");
  });

  it("still says something when what was thrown is not an error at all", () => {
    expect(refusal("nope")).toBe("The camera could not be opened.");
  });
});
