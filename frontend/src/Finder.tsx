/** The scan page's search box: find an item by its words, or type or scan its code. */
import { msg } from "@lingui/core/macro";
import type { Signal } from "@preact/signals";
import { type Catalogue, RETIRED, search } from "./cart";
import { Field } from "./Field";
import { say } from "./i18n";
import { readLabel, type Scanned } from "./labels";

export const FIND = msg`Find an item, or type or scan its code`;
export const NOTHING_MATCHES = msg`Nothing matches “{typed}”.`;

export function Finder({
  catalogue,
  query,
  onAdd,
  onLabel,
}: {
  catalogue: Catalogue;
  query: Signal<string>;
  onAdd: (itemId: number, packageId?: number) => void;
  /** A label's code typed, as a scanner gun types one. */
  onLabel: (read: Scanned) => void;
}) {
  const matches = search(catalogue, query.value);
  return (
    <>
      <Field label={say(FIND)}>
        {(id) => (
          <input
            id={id}
            type="search"
            autoComplete="off"
            autoCapitalize="none"
            spellcheck={false}
            value={query.value}
            onInput={(event) => {
              query.value = event.currentTarget.value;
            }}
            onKeyDown={(event) => {
              // A scanner gun types the code, then Enter.
              if (event.key !== "Enter") {
                return;
              }
              event.preventDefault();
              const read = readLabel(catalogue, query.value);
              if (read.kind !== "unknown") {
                onLabel(read);
              } else if (matches.length === 1 && matches[0] !== undefined) {
                onAdd(matches[0].id);
              }
            }}
          />
        )}
      </Field>
      {query.value.trim() !== "" && matches.length === 0 && (
        <p>{say(NOTHING_MATCHES, { typed: query.value.trim() })}</p>
      )}
      {matches.length > 0 && (
        <ul class="list-none p-0 *:py-1">
          {matches.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onAdd(item.id)}>
                {item.name}
              </button>{" "}
              <small>
                {[
                  item.retired ? say(RETIRED) : null,
                  item.category,
                  item.manufacturer,
                  item.part_number,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
