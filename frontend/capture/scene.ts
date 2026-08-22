import { manage } from "../integration/django";

/**
 * The rest of the scene, added to the seeded one and the same on every run.
 *
 * `seed_integration_data` gives the browser suite a login, a volunteer, an
 * item and a warehouse. A guide needs three things it does not carry: a
 * sticker to scan, some stock to take off a shelf, and something measured,
 * whose scan asks how much before anything goes in the batch. All three are
 * put here rather than in the seed so that the five specs that depend on that
 * scene keep depending on exactly what they did before.
 *
 * Fixed, not minted. A code drawn at random would be a different code in every
 * picture, so regenerating the images would rewrite committed binaries that
 * nothing about the app had changed. The first two are the codes decision 0011
 * and `LabelSheetView` already use as examples; the third is chosen once, here,
 * and never changes for the same reason.
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

/**
 * The sticker on a box of cable: the same gesture, answered by a dialog.
 *
 * Anything whose unit is not `each` is entered rather than counted (decision
 * 0011 section 5), and that dialog is the only modal the volunteer's app has.
 * The guide could not show it without something measured in the scene.
 */
export const MEASURED_CODE = "8XJ4T6Y1KD";
export const MEASURED_ITEM = "ToughCable Pro";
export const MEASURED_UNIT = "metre";
/** What a full box holds, which is what the dialog offers as a bearing. */
export const MEASURED_FULL = 305;
/** What the pictures take off it: part of a box, which is the ordinary case. */
export const MEASURED_TAKEN = 30;

/** What the shelf holds when the pictures are taken. */
export const ON_HAND = 12;

/**
 * And of the measured item. Enough for `MEASURED_TAKEN` twice over, so the
 * second line of the batch is not itself worth a stock count -- one warning is
 * what the picture of one is about.
 */
export const MEASURED_ON_HAND = MEASURED_FULL;

/** The batch in the "worth a stock count" shot takes more than there is. */
export const MORE_THAN_IS_THERE = 20;

/** The two spellings the administrator's guide merges one into the other. */
export const NEAR_MISS = ["Aidan", "Aiden"];

/**
 * Put both shelves back to what the pictures need and make sure everything
 * else one needs is there: the three stickers, the measured item they are on,
 * a couple of identifiers, and the questions the sheet import leaves behind on
 * an item and on a pair of volunteers.
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
      // Something measured, so the dialog the volunteer's guide shows has
      // something to open over. Its category is the seeded item's: a category
      // of its own would be a second thing to keep fixed for no picture's sake.
      `cable, _ = Item.objects.get_or_create(name=${quoted(MEASURED_ITEM)}, defaults={"category": item.category, "unit_of_measure": ${quoted(MEASURED_UNIT)}})`,
      `Label.objects.get_or_create(code=${quoted(MEASURED_CODE)}, defaults={"item": cable, "quantity": ${MEASURED_FULL}})`,
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
      "def declare(target, wanted):",
      "    held = StockBalance.objects.filter(item=target, location=place).first()",
      "    short = Decimal(wanted) - (held.quantity if held else Decimal(0))",
      "    if not short:",
      "        return",
      // A count, because that is what this is: the shelf is being declared,
      // not received from anybody or given to anybody. Which side the
      // difference goes on is its direction -- the quantity is always
      // positive, which is the thing the administrator's guide has to explain.
      '    counted = StockTransaction.objects.create(kind="count", actor=who, reason="Dressing the scene the guides are drawn from")',
      '    side = "to_location" if short > 0 else "from_location"',
      "    StockMovement.objects.create(transaction=counted, item=target, quantity=abs(short), **{side: place})",
      `declare(item, ${ON_HAND})`,
      `declare(cable, ${MEASURED_ON_HAND})`,
    ].join("\n"),
  );
}

/** A Python string literal. The codes are ours, but the quoting should not be. */
function quoted(value: string): string {
  return JSON.stringify(value);
}
