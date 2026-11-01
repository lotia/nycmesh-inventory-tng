import { render, screen } from "@testing-library/preact";
import { expect, test } from "vitest";
import { RegisterPage } from "./RegisterPage";
import { ScanPage } from "./ScanPage";

test("the scan page has its heading", () => {
  render(<ScanPage />);
  expect(screen.getByRole("heading", { name: "Scan" })).toBeDefined();
});

test("the registration page has its heading", () => {
  render(<RegisterPage />);
  expect(
    screen.getByRole("heading", { name: "Register this device" }),
  ).toBeDefined();
});
