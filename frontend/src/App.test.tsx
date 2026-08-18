import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";

describe("App", () => {
  it("renders the application heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /nyc mesh inventory/i })).toBeInTheDocument();
  });

  it("tells the reader that no inventory features exist yet", () => {
    render(<App />);
    expect(screen.getByRole("alert")).toHaveTextContent(/no inventory features/i);
  });
});
