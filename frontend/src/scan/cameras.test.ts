/**
 * Which camera the app asks for, and how it knows there is one.
 *
 * The lens a phone hands back for `facingMode: "environment"` is the thing
 * this cannot test and the thing decision 0011 warns about, so what is tested
 * here is that the volunteer's own choice is asked for exactly, kept, and
 * offered again next time.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cameraIsGone,
  cameraSupported,
  constraints,
  forgetCamera,
  IDEAL_WIDTH,
  listCameras,
  loadCamera,
  saveCamera,
} from "./cameras";
import { audioInput, clearMediaDevices, stubMediaDevices, videoInput } from "./testFixtures";

afterEach(() => {
  clearMediaDevices();
  window.localStorage.clear();
});

describe("whether a camera can be opened", () => {
  it("is no on an insecure origin, where the API is missing rather than refused", () => {
    expect(cameraSupported()).toBe(false);
  });

  it("is yes where the browser offers one", () => {
    stubMediaDevices({ getUserMedia: vi.fn() });
    expect(cameraSupported()).toBe(true);
  });
});

describe("the cameras on offer", () => {
  it("lists the video inputs and nothing else", async () => {
    stubMediaDevices({
      enumerateDevices: async () => [
        videoInput("back", "Back Camera"),
        audioInput("mic"),
        videoInput("front", "Front Camera"),
      ],
    });
    expect(await listCameras()).toEqual([
      { deviceId: "back", label: "Back Camera" },
      { deviceId: "front", label: "Front Camera" },
    ]);
  });

  it("names them by position before permission has been granted", async () => {
    stubMediaDevices({
      enumerateDevices: async () => [videoInput("a"), videoInput("b")],
    });
    expect((await listCameras()).map((camera) => camera.label)).toEqual(["Camera 1", "Camera 2"]);
  });
});

describe("what getUserMedia is asked for", () => {
  it("asks for the back camera when nobody has chosen one", () => {
    expect(constraints(null)).toEqual({
      video: { width: { ideal: IDEAL_WIDTH }, facingMode: "environment" },
    });
  });

  it("asks for a chosen camera exactly, so nothing can substitute another lens", () => {
    expect(constraints("back")).toEqual({
      video: { width: { ideal: IDEAL_WIDTH }, deviceId: { exact: "back" } },
    });
  });

  it("asks for the resolution as a preference and not a requirement", () => {
    // Whichever lens is being asked for: the width carries `ideal` and
    // nothing else, for the reason `constraints` gives.
    for (const asked of [constraints(null), constraints("back")]) {
      expect((asked.video as MediaTrackConstraints).width).toEqual({ ideal: IDEAL_WIDTH });
    }
  });
});

describe("the camera this device last used", () => {
  it("is nothing until one is chosen", () => {
    expect(loadCamera()).toBeNull();
  });

  it("is remembered, so a phone that answers badly costs one tap ever", () => {
    saveCamera("back");
    expect(loadCamera()).toBe("back");
  });

  it("is ignored if what was stored is not a device id", () => {
    window.localStorage.setItem("nycmesh-inventory.camera.v1", "{}");
    expect(loadCamera()).toBeNull();
  });

  it("is forgotten when it stops existing, so the choice is not a dead end", () => {
    saveCamera("back");
    forgetCamera();
    expect(loadCamera()).toBeNull();
  });
});

describe("a camera that is not there", () => {
  it("is what the browser says by name, whatever class it threw", () => {
    expect(cameraIsGone(new DOMException("", "NotFoundError"))).toBe(true);
    expect(cameraIsGone(new DOMException("", "OverconstrainedError"))).toBe(true);
  });

  it("is not every other way the camera can refuse", () => {
    expect(cameraIsGone(new DOMException("", "NotAllowedError"))).toBe(false);
    expect(cameraIsGone("nope")).toBe(false);
  });
});
