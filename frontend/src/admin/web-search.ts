import { follow } from "./follow-fields";

/**
 * The Search the web link beside an item's manufacturer and part number
 * (catalogue/_web_search.html), kept current as they are typed in: its
 * address searches for what they hold, and it is hidden while both are
 * blank. Without this, it searches for what the page was sent with.
 */
export function followWebSearch(document: Document): void {
  for (const shown of document.querySelectorAll<HTMLElement>(
    "[data-web-search]",
  )) {
    const link = shown.querySelector("a");
    const form = shown.closest("form");
    if (link === null || form === null) {
      continue;
    }
    const fields = ["manufacturer", "part_number"]
      .map((name) => form.elements.namedItem(name))
      .filter((field) => field instanceof HTMLInputElement);
    follow(document, fields, () => {
      const query = fields
        .map((field) => field.value.trim())
        .filter((value) => value !== "")
        .join(" ");
      const address = new URL(link.href);
      address.searchParams.set("q", query);
      link.href = address.href;
      shown.hidden = query === "";
    });
  }
}
