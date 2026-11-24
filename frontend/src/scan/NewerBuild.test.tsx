import { fireEvent, render, screen } from "@testing-library/preact";
import { expect, test } from "vitest";
import { UPGRADE_BUTTON, UpgradeToSave } from "./NewerBuild";

test("with no Upgrade button on the page, the link makes its own jump", () => {
  render(<UpgradeToSave />);
  const link = screen.getByRole("link", { name: "Upgrade" });
  expect(link.getAttribute("href")).toBe(`#${UPGRADE_BUTTON}`);
  // fireEvent answers false once the default is prevented.
  expect(fireEvent.click(link)).toBe(true);
});
