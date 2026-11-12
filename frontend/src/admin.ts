/**
 * What the administrator pages do in the browser beyond plain HTML, which
 * they still work as without it (docs/decisions/0019).
 */
import { suggest } from "./combobox";
import { followPicks, followTyped } from "./hints";
import { guardRetire } from "./retire";

guardRetire(document);
followPicks(document);
followTyped(document);
for (const input of document.querySelectorAll<HTMLInputElement>(
  "input[data-suggest]",
)) {
  suggest(input, { go: (href) => location.assign(href) });
}
