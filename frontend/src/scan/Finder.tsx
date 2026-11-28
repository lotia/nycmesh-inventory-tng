/** The scan page's search box: find an item by its words, or type or scan
 * its code. While this build is too old, nothing it picks or reads is
 * added, as the camera reads nothing then (Ali, 2026-11-24,
 * inventory-tng-n1p8). */
import { msg } from "@lingui/core/macro";
import { type ReadonlySignal, useSignal } from "@preact/signals";
import type { ComponentChildren, RefObject } from "preact";
import { Field } from "../shared/Field";
import { say } from "../shared/i18n";
import { focusAndIgnoreTaps } from "../shared/settling";
import { type Cart, type Catalogue, RETIRED } from "./cart";
import { readLabel, type Scanned } from "./label-codes";
import { UpgradeToSave } from "./NewerBuild";
import { type About, useClearedWhenLineChanges } from "./refused-line";
import { search } from "./search";

export const FIND = msg`Find an item, or type or scan its code`;
export const NOTHING_MATCHES = msg`Nothing matches “{typed}”.`;

/** What a pick did, in words, and whether it was refused, adding nothing;
 * refused, the cart line it is about, if any (refused-line.ts). */
export type Picked = {
  said: string;
  refused: boolean;
  about?: About | undefined;
};
export const done = (said: string): Picked => ({ said, refused: false });
export const refused = (said: string, about?: About): Picked => ({
  said,
  refused: true,
  about,
});

export function Finder({
  catalogue,
  cart,
  onAdd,
  onLabel,
  tooOld,
  after,
}: {
  catalogue: Catalogue;
  /** The cart as it is now, whose lines a refusal may be about. */
  cart: Cart;
  onAdd: (itemId: number, packageId?: number) => Picked;
  /** A label's code typed, as a scanner gun types one. */
  onLabel: (read: Scanned) => Picked;
  /** Whether the server takes nothing more from this build (NewerBuild). */
  tooOld: ReadonlySignal<boolean>;
  /** What follows the matches, the Cart heading, focused once a tap on
   * one takes them away: the search box would bring up the keyboard. */
  after: RefObject<HTMLElement | null>;
}) {
  // Emptied only by a pick or a code typed here, never by a label the
  // camera reads while the volunteer types (inventory-tng-tjyo).
  const query = useSignal("");
  const matches = search(catalogue, query.value);
  // A refused pick's words, under the search as the scanner says them, till
  // the next pick, typing, or a change to the line it is about; a pick that
  // adds needs none, as its line shows it. Too old, every pick is refused,
  // saying to upgrade.
  const refusal = useSignal<ComponentChildren>("");
  const refusing = useClearedWhenLineChanges(cart, () => {
    refusal.value = "";
  });
  const show = (pick: () => Picked) => {
    query.value = "";
    if (tooOld.value) {
      refusal.value = <UpgradeToSave />;
      refusing();
      return;
    }
    const picked = pick();
    refusal.value = picked.refused ? picked.said : "";
    refusing(picked.about);
  };
  return (
    <>
      <div>
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
                const [only] = matches;
                if (read.kind !== "unknown") {
                  show(() => onLabel(read));
                } else if (matches.length === 1 && only !== undefined) {
                  show(() => onAdd(only.id));
                }
              }}
            />
          )}
        </Field>
        {/* Always there, as the scanner's status is, so a refusal is read
            out as it appears, and keeping its room (styles.css). */}
        <p role="status" class="status-line mt-2 mb-0">
          {refusal.value}
        </p>
      </div>
      {query.value.trim() !== "" && matches.length === 0 && (
        <p>{say(NOTHING_MATCHES, { typed: query.value.trim() })}</p>
      )}
      {matches.length > 0 && (
        <ul class="list-none p-0 *:py-1">
          {matches.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => {
                  show(() => onAdd(item.id));
                  // The matches go, and what is below moves up under the
                  // finger (Ali, 2026-11-27, inventory-tng-r3jx).
                  focusAndIgnoreTaps(document, () => after.current);
                }}
              >
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
