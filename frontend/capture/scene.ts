import { manage } from "../integration/django";

/**
 * The rest of the scene, added to the seeded one and the same on every run.
 *
 * `seed_integration_data` gives the browser suite a login, a volunteer, an
 * item and a warehouse. A guide needs two things it does not carry: a sticker
 * to scan, and some stock to take off a shelf. Both are put here rather than
 * in the seed so that the five specs that depend on that scene keep depending
 * on exactly what they did before.
 *
 * Fixed, not minted. A code drawn at random would be a different code in every
 * picture, so regenerating the images would rewrite committed binaries that
 * nothing about the app had changed. These two are the codes decision 0011 and
 * `LabelSheetView` already use as examples.
 *
 * Through `manage.py` rather than the API, because this runs before a browser
 * exists to hold a session -- the same reason `integration/qrVideo.ts` mints
 * through Django.
 */

/** The sticker on the box, and what one scan of it stands for. */
export const ITEM_CODE = "7QK3M2XV9A";
export const ITEM_LABEL_QUANTITY = 5;

/** The sticker on the wall, which stands for the place and no quantity. */
export const WALL_CODE = "4NP8R7T2WQ";

/** What the shelf holds when the pictures are taken. */
export const ON_HAND = 12;

/** The batch in the "worth a stock count" shot takes more than there is. */
export const MORE_THAN_IS_THERE = 20;

/** The two spellings the administrator's guide merges one into the other. */
export const NEAR_MISS = ["Aidan", "Aiden"];

/**
 * Put the shelf back to `ON_HAND` and make sure everything a picture needs is
 * there: both stickers, a couple of identifiers, and the questions the sheet
 * import leaves behind on an item and on a pair of volunteers.
 *
 * Idempotent in the only way an append-only ledger can be: the difference
 * between what is on the shelf and what the pictures need is posted as a
 * count, which is how anybody else says the shelf disagrees with the system.
 * A run that changes nothing posts nothing.
 *
 * The two flags are written by the import's own functions rather than by
 * sentences copied out of them. An underscore is not usually an invitation,
 * but a guide showing an administrator a question in words the import does not
 * actually use would be teaching them to look for the wrong thing.
 */
export function dressTheScene(item: number, location: number, volunteer: number): void {
  manage(
    "shell",
    "-c",
    [
      "from decimal import Decimal",
      "from inventory.management.commands._people import _flag",
      "from inventory.management.commands._quantities import flag",
      "from inventory.models import Item, ItemIdentifier, Label, Location, StockBalance, StockMovement, StockTransaction, Volunteer",
      `item = Item.objects.get(pk=${item})`,
      `place = Location.objects.get(pk=${location})`,
      `who = Volunteer.objects.get(pk=${volunteer})`,
      `Label.objects.get_or_create(code=${quoted(ITEM_CODE)}, defaults={"item": item, "quantity": ${ITEM_LABEL_QUANTITY}})`,
      `Label.objects.get_or_create(code=${quoted(WALL_CODE)}, defaults={"location": place, "quantity": None})`,
      'ItemIdentifier.objects.get_or_create(value="LBE-5AC-GEN2", defaults={"item": item, "kind": "mfg_part"})',
      'ItemIdentifier.objects.get_or_create(value="litebeam", defaults={"item": item, "kind": "alias"})',
      // The census the sheet import would have taken over this item, so the
      // flag reads as one an administrator will really meet.
      "census = [(item, Decimal(1))] * 30 + [(item, Decimal(100))] * 17 + [(item, Decimal(200))] * 9",
      "flag(census, [item])",
      `spelled = {name.lower(): name for name in ${quoted(NEAR_MISS.join(","))}.split(",")}`,
      "for key, name in spelled.items():",
      "    other = tuple(k for k in spelled if k != key)",
      '    Volunteer.objects.update_or_create(sheet_key=key, defaults={"display_name": name, "sheet_flag": _flag(spelled, other)})',
      "held = StockBalance.objects.filter(item=item, location=place).first()",
      `short = Decimal(${ON_HAND}) - (held.quantity if held else Decimal(0))`,
      "if short:",
      // A count, because that is what this is: the shelf is being declared,
      // not received from anybody or given to anybody.
      '    counted = StockTransaction.objects.create(kind="count", actor=who, reason="Dressing the scene the guides are drawn from")',
      '    side = "to_location" if short > 0 else "from_location"',
      "    StockMovement.objects.create(transaction=counted, item=item, quantity=abs(short), **{side: place})",
    ].join("\n"),
  );
}

/** A Python string literal. The codes are ours, but the quoting should not be. */
function quoted(value: string): string {
  return JSON.stringify(value);
}
