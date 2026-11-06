import type { ComponentChild } from "preact";
import { render } from "preact";
import "./styles.css";
import "./app.css";

/** Render a page into the element the backend's template leaves for it. */
export function mount(page: ComponentChild): void {
  const root = document.getElementById("app");
  if (root === null) {
    throw new Error("The page has no #app element");
  }
  render(page, root);
}
