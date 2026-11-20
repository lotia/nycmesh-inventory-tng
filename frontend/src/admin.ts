/**
 * What the administrator pages do in the browser beyond plain HTML, which
 * they still work as without it (docs/decisions/0019).
 */
import { copyButtons, printButtons } from "./admin/buttons";
import { suggest } from "./admin/combobox";
import { followPicks, followTyped, followWas } from "./admin/hints";
import { guardRetire } from "./admin/retire";
import { sendOnce } from "./admin/single-use-forms";
import { followWebSearch } from "./admin/web-search";

guardRetire(document);
sendOnce(window);
printButtons(document);
copyButtons(document);
followPicks(document);
followTyped(document);
followWas(document);
followWebSearch(document);
for (const input of document.querySelectorAll<HTMLInputElement>(
  "input[data-suggest]",
)) {
  suggest(input, { go: (href) => location.assign(href) });
}
