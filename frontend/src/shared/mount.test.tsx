import { afterEach, describe, expect, it } from "vitest";
import { mount } from "./mount";

describe("mount", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  it("renders the page into #app", () => {
    document.body.innerHTML = '<div id="app"></div>';
    mount(<p>Hello</p>);
    expect(document.getElementById("app")?.textContent).toBe("Hello");
  });

  it("fails when the template left no #app", () => {
    expect(() => mount(<p>Hello</p>)).toThrow("The page has no #app element");
  });
});
