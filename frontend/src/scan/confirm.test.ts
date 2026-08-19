/**
 * The two answers a volunteer gets without looking up.
 *
 * jsdom has neither a speaker nor a vibration motor, so what is asserted is
 * the calls: that a tone is scheduled and stopped, that a batch of scans opens
 * one audio device rather than one each, and that a device with no Web Audio
 * at all still gets the buzz and no exception.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { confirmScan, forgetAudio } from "./confirm";

/** Just enough Web Audio to record what was asked of it. */
class FakeNode {
  connect = vi.fn((node: unknown) => node);
}

class FakeOscillator extends FakeNode {
  frequency = { value: 0 };
  start = vi.fn();
  stop = vi.fn();
  onended: (() => void) | null = null;
}

class FakeGain extends FakeNode {
  gain = { setValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn() };
}

class FakeAudioContext {
  static last: FakeAudioContext | null = null;
  currentTime = 10;
  destination = {};
  oscillator = new FakeOscillator();
  gainNode = new FakeGain();
  close = vi.fn();
  resume = vi.fn();

  constructor() {
    FakeAudioContext.last = this;
  }

  createOscillator = () => this.oscillator;
  createGain = () => this.gainNode;
}

function vibration(): ReturnType<typeof vi.fn> {
  const vibrate = vi.fn();
  Object.defineProperty(navigator, "vibrate", { value: vibrate, configurable: true });
  return vibrate;
}

afterEach(() => {
  // The context is shared for the life of the module, which is the point of
  // it -- so each test has to start from none.
  forgetAudio();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "vibrate");
  FakeAudioContext.last = null;
});

describe("confirming a scan", () => {
  it("buzzes the phone", () => {
    const vibrate = vibration();
    confirmScan();
    expect(vibrate).toHaveBeenCalledWith(40);
  });

  it("plays a tone that starts and ends", () => {
    vi.stubGlobal("AudioContext", FakeAudioContext);
    confirmScan();

    const context = FakeAudioContext.last as FakeAudioContext;
    expect(context.oscillator.frequency.value).toBeGreaterThan(0);
    expect(context.oscillator.start).toHaveBeenCalled();
    expect(context.oscillator.stop).toHaveBeenCalledWith(context.currentTime + 0.09);
    expect(context.gainNode.gain.exponentialRampToValueAtTime).toHaveBeenCalled();
  });

  it("opens one audio device for the whole batch, not one per scan", () => {
    // A phone allows only a handful, and a context made before the tab has
    // been interacted with starts suspended -- so its clock never advances,
    // an oscillator scheduled on it never ends, and anything hung off
    // `onended` never runs. Closing per scan is how the handful runs out.
    vi.stubGlobal("AudioContext", FakeAudioContext);
    const opened: FakeAudioContext[] = [];

    for (let scan = 0; scan < 5; scan += 1) {
      confirmScan();
      opened.push(FakeAudioContext.last as FakeAudioContext);
    }

    expect(new Set(opened).size).toBe(1);
    expect(opened[0].close).not.toHaveBeenCalled();
  });

  it("resumes the device once scanning is the interaction that allows it", () => {
    vi.stubGlobal("AudioContext", FakeAudioContext);
    confirmScan();

    expect((FakeAudioContext.last as FakeAudioContext).resume).toHaveBeenCalled();
  });

  it("is silent rather than broken where the tab may not make a sound", () => {
    const vibrate = vibration();
    vi.stubGlobal(
      "AudioContext",
      class {
        constructor() {
          throw new Error("not allowed to start");
        }
      },
    );
    expect(() => confirmScan()).not.toThrow();
    expect(vibrate).toHaveBeenCalled();
  });

  it("does not need a vibration motor either", () => {
    expect(() => confirmScan()).not.toThrow();
  });
});
