/**
 * What the administrator pages do in the browser beyond plain HTML, which
 * they still work as without it (docs/decisions/0019).
 */
import { copyButtons, printButtons } from "./admin/buttons";
import { suggest } from "./admin/combobox";
import { followPicks, followTyped, followWas } from "./admin/hints";
import { sendOnce } from "./admin/once";
import { guardRetire } from "./admin/retire";

guardRetire(document);
sendOnce(window);
printButtons(document);
copyButtons(document);
followPicks(document);
followTyped(document);
followWas(document);
for (const input of document.querySelectorAll<HTMLInputElement>(
  "input[data-suggest]",
)) {
  suggest(input, { go: (href) => location.assign(href) });
}
