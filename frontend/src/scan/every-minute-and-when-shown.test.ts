import { renderHook } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { show, stubVisibility } from "../testing/visibility";
import {
  CHECK_EVERY_MS,
  useEveryMinuteAndWhenShown,
} from "./every-minute-and-when-shown";

describe("useEveryMinuteAndWhenShown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stubVisibility();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  test("checks once each interval", () => {
    const check = vi.fn();
    renderHook(() => useEveryMinuteAndWhenShown(check, []));
    vi.advanceTimersByTime(CHECK_EVERY_MS - 1);
    expect(check).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(check).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(CHECK_EVERY_MS);
    expect(check).toHaveBeenCalledTimes(2);
  });

  test("checks when the page is shown, never while hidden", () => {
    const check = vi.fn();
    renderHook(() => useEveryMinuteAndWhenShown(check, []));
    show("hidden");
    vi.advanceTimersByTime(CHECK_EVERY_MS);
    expect(check).not.toHaveBeenCalled();
    show("visible");
    expect(check).toHaveBeenCalledTimes(1);
  });

  test("runs the latest check", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = renderHook(
      ({ check }) => useEveryMinuteAndWhenShown(check, []),
      { initialProps: { check: first } },
    );
    rerender({ check: second });
    show("visible");
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  test("starts again, once, when deps change", () => {
    const check = vi.fn();
    const { rerender } = renderHook(
      ({ dep }) => useEveryMinuteAndWhenShown(check, [dep]),
      { initialProps: { dep: 1 } },
    );
    vi.advanceTimersByTime(CHECK_EVERY_MS - 1);
    rerender({ dep: 2 });
    vi.advanceTimersByTime(CHECK_EVERY_MS - 1);
    expect(check).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(check).toHaveBeenCalledTimes(1);
    show("visible");
    expect(check).toHaveBeenCalledTimes(2);
  });

  test("stops on unmount", () => {
    const check = vi.fn();
    const { unmount } = renderHook(() => useEveryMinuteAndWhenShown(check, []));
    unmount();
    vi.advanceTimersByTime(CHECK_EVERY_MS);
    show("visible");
    expect(check).not.toHaveBeenCalled();
  });
});
