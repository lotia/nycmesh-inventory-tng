import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadVolunteer, STORAGE_KEY, saveVolunteer } from "./rememberedVolunteer";

const sean = { id: 7, display_name: "Sean McGinnis", email: null, slack_id: null };

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the remembered volunteer", () => {
  it("comes back after the phone has been locked and the app reloaded", () => {
    saveVolunteer(sean);
    expect(loadVolunteer()).toEqual({ id: 7, displayName: "Sean McGinnis" });
  });

  it("is nobody until somebody has been picked", () => {
    expect(loadVolunteer()).toBeNull();
  });

  it("hands back what it stored, so the caller and the store agree", () => {
    expect(saveVolunteer(sean)).toEqual({ id: 7, displayName: "Sean McGinnis" });
  });

  it("ignores something stored in another shape rather than restoring a name-less id", () => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ id: 7 }));
    expect(loadVolunteer()).toBeNull();
  });

  it("ignores unparseable storage", () => {
    window.localStorage.setItem(STORAGE_KEY, "{{{");
    expect(loadVolunteer()).toBeNull();
  });

  it("works where storage is denied outright, as it is in private browsing", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(() => saveVolunteer(sean)).not.toThrow();
    expect(loadVolunteer()).toBeNull();
  });
});
