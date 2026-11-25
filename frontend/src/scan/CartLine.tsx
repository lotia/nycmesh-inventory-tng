/** One line of the cart: its item, how much of it, and the package it opens. */
import { msg } from "@lingui/core/macro";
import { Fragment, type Ref } from "preact";
import { useId } from "preact/hooks";
import { countedWhole, keyboard } from "../amount-rules";
import { Field } from "../shared/Field";
import { say, sayAmount } from "../shared/i18n";
import { wordsOf } from "../shared/outbox-notes";
import { thousandths } from "../shared/quantity";
import {
  type Box,
  type Item,
  type Line,
  quantityOf,
  RETIRED,
  resized,
  unitChanged,
} from "./cart";
import { AlertWords } from "./Notes";
import { NOTHING_TAKEN, OpenPackageCard } from "./OpenPackageCard";
import { type Offered, takingOffered } from "./offered";

export const LINE_GONE = msg`No longer in the catalogue.`;
export const REMOVE = msg`Remove`;
export const RESIZED_TO = msg`The package size has changed to {size} since this item was added. Enter the count again.`;
/** Beside a line whose amounts were typed in a unit the item is no longer
 * counted in, and so cleared (Ali, 2026-11-23, inventory-tng-h7ua). */
export const UNIT_CHANGED = msg`The unit changed from {was} to {unit}. Enter the amount again in {unit}.`;
export const PACKAGE_COUNT = msg`{package} ({size} {unit})`;
export const PACKAGE_COUNT_RETIRED = msg`{package} ({size} {unit}), retired: taking only`;
export const LOOSE_UNITS = msg`Loose units`;
export const LOOSE_IN = msg`Loose {unit}`;
export const HOW_MANY = msg`How many`;
export const HOW_MANY_IN = msg`How many {unit}`;
export const IN_ALL = msg`{amount} {unit} in all`;

/** Whether two boxes are the same box of a line. */
function sameBox(a: Box, b: Box): boolean {
  switch (a.kind) {
    case "loose":
      return b.kind === "loose";
    case "package":
      return b.kind === "package" && b.packageId === a.packageId;
    case "open":
      return b.kind === "open" && b.key === a.key;
  }
}

export function CartLine({
  line,
  item,
  opens,
  moving,
  onChange,
  onRemove,
  removeRef = null,
}: {
  line: Line;
  item: Item | undefined;
  /** Its open packages where it comes from, to take from. */
  opens: Offered[];
  /** Whether an open package can move whole. */
  moving: boolean;
  onChange: (line: Line) => void;
  onRemove: () => void;
  /** Given its Remove button, to focus (CartForm, after Undo). */
  removeRef?: Ref<HTMLButtonElement>;
}) {
  // What the line comes to, or why it cannot, read with the box refused
  // (aria-describedby); and the loose box's own refusal, when another
  // comes first (cart.Refusal).
  const sumId = useId();
  const looseId = useId();
  if (item === undefined) {
    return (
      <li>
        {say(LINE_GONE)}{" "}
        <button type="button" ref={removeRef} onClick={onRemove}>
          {say(REMOVE)}
        </button>
      </li>
    );
  }
  // Read as saved (takingOffered): only the open packages shown.
  const quantity = quantityOf(
    takingOffered(line, new Set(opens.map((open) => open.key))),
    item,
  );
  const refusal = "problem" in quantity ? quantity : null;
  /** What says why `box` is refused, if it is; read with it. */
  const refusing = (box: Box): string | undefined => {
    const shown = refusal?.box;
    if (shown !== undefined && sameBox(shown, box)) {
      return sumId;
    }
    return box.kind === "loose" && refusal?.looseProblem ? looseId : undefined;
  };
  const looseSaid = refusing({ kind: "loose" });
  return (
    <li class="flex flex-col gap-2">
      <div>
        <strong>{item.name}</strong>
        {item.retired && <small> {say(RETIRED)}</small>}
      </div>
      {unitChanged(line, item) && (
        <p role="alert">
          <AlertWords
            note={{
              bold: [say(UNIT_CHANGED, { was: line.unit, unit: item.unit })],
            }}
          />
        </p>
      )}
      {opens.map((open) => (
        <OpenPackageCard
          key={open.key}
          open={open}
          item={item}
          moving={moving}
          taken={line.open?.[open.key] ?? NOTHING_TAKEN}
          refusedBy={refusing({ kind: "open", key: open.key })}
          onTake={(next) =>
            onChange({ ...line, open: { ...line.open, [open.key]: next } })
          }
        />
      ))}
      {item.packages.map((pack) => {
        // The catalogue gives every size as a decimal, so never null.
        /* v8 ignore next */
        const size = sayAmount(thousandths(pack.size) ?? 0);
        const said = refusing({ kind: "package", packageId: pack.id });
        return (
          <Fragment key={pack.id}>
            {resized(line, pack) && (
              <p role="alert">
                <AlertWords
                  note={{
                    bold: [say(RESIZED_TO, { size })],
                  }}
                />
              </p>
            )}
            <Field
              quiet
              label={say(pack.retired ? PACKAGE_COUNT_RETIRED : PACKAGE_COUNT, {
                package: pack.name,
                size,
                unit: item.unit,
              })}
            >
              {(id) => (
                <input
                  id={id}
                  inputMode="numeric"
                  aria-invalid={said !== undefined || undefined}
                  aria-describedby={said}
                  value={line.packages[pack.id] ?? ""}
                  onInput={(event) =>
                    onChange({
                      ...line,
                      packages: {
                        ...line.packages,
                        [pack.id]: event.currentTarget.value,
                      },
                    })
                  }
                />
              )}
            </Field>
          </Fragment>
        );
      })}
      <Field
        quiet
        label={
          countedWhole(item.unit)
            ? say(item.packages.length > 0 ? LOOSE_UNITS : HOW_MANY)
            : say(item.packages.length > 0 ? LOOSE_IN : HOW_MANY_IN, {
                unit: item.unit,
              })
        }
      >
        {(id) => (
          <input
            id={id}
            inputMode={keyboard(item.unit)}
            aria-invalid={looseSaid !== undefined || undefined}
            aria-describedby={looseSaid}
            value={line.loose}
            onInput={(event) =>
              onChange({ ...line, loose: event.currentTarget.value })
            }
          />
        )}
      </Field>
      <div id={sumId}>
        {"problem" in quantity
          ? wordsOf(quantity.problem)
          : say(IN_ALL, {
              amount: sayAmount(quantity.amount),
              unit: item.unit,
            })}
      </div>
      {refusal?.looseProblem && (
        <div id={looseId}>{wordsOf(refusal.looseProblem)}</div>
      )}
      <button
        type="button"
        class="self-start"
        ref={removeRef}
        onClick={onRemove}
      >
        {say(REMOVE)}
      </button>
    </li>
  );
}
