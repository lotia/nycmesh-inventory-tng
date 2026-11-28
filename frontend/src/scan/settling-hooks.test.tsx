import { act, renderHook } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { SETTLE_MS } from "../shared/settling";
import { useKeptSpace } from "./settling-hooks";

describe("useKeptSpace", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  test("kept again at the same height, the space stays SETTLE_MS from then", () => {
    const { result } = renderHook(() => useKeptSpace());
    act(() => result.current.keep(40));
    act(() => {
      vi.advanceTimersByTime(SETTLE_MS - 100);
    });
    act(() => result.current.keep(40));
    act(() => {
      vi.advanceTimersByTime(SETTLE_MS - 1);
    });
    expect(result.current.space(() => {})).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current.space(() => {})).toBeNull();
  });
});
