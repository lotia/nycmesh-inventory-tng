/** The scan page's search box: find an item by its words, or type or scan its code. */
import { msg } from "@lingui/core/macro";
import { type Signal, useSignal } from "@preact/signals";
import { Field } from "../shared/Field";
import { say } from "../shared/i18n";
import { type Catalogue, RETIRED } from "./cart";
import { readLabel, type Scanned } from "./label-codes";
import { search } from "./search";

export const FIND = msg`Find an item, or type or scan its code`;
export const NOTHING_MATCHES = msg`Nothing matches “{typed}”.`;

/** What a pick did, in words, and whether it was refused, adding nothing. */
export type Picked = { said: string; refused: boolean };

export function Finder({
  catalogue,
  query,
  onAdd,
  onLabel,
}: {
  catalogue: Catalogue;
  query: Signal<string>;
  onAdd: (itemId: number, packageId?: number) => Picked;
  /** A label's code typed, as a scanner gun types one. */
  onLabel: (read: Scanned) => Picked;
}) {
  const matches = search(catalogue, query.value);
  // A refused pick's words, under the search as the scanner says them, till
  // the next change; a pick that adds needs none, as its line shows it.
  const refusal = useSignal("");
  const show = (picked: Picked) => {
    refusal.value = picked.refused ? picked.said : "";
  };
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
              refusal.value = "";
            }}
            onKeyDown={(event) => {
              // A scanner gun types the code, then Enter.
              if (event.key !== "Enter") {
                return;
              }
              event.preventDefault();
              const read = readLabel(catalogue, query.value);
              if (read.kind !== "unknown") {
                show(onLabel(read));
              } else if (matches.length === 1 && matches[0] !== undefined) {
                show(onAdd(matches[0].id));
              }
            }}
          />
        )}
      </Field>
      {refusal.value !== "" && <p role="status">{refusal.value}</p>}
      {query.value.trim() !== "" && matches.length === 0 && (
        <p>{say(NOTHING_MATCHES, { typed: query.value.trim() })}</p>
      )}
      {matches.length > 0 && (
        <ul class="list-none p-0 *:py-1">
          {matches.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => show(onAdd(item.id))}>
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
