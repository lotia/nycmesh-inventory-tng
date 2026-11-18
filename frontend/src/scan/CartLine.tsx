/** One line of the cart: its item, how much of it, and the package it opens. */
import { msg } from "@lingui/core/macro";
import { Fragment } from "preact";
import { Field } from "../shared/Field";
import { say } from "../shared/i18n";
import { wordsOf } from "../shared/outbox";
import { decimal, thousandths } from "../shared/quantity";
import { type Item, type Line, quantityOf, RETIRED, resized } from "./cart";
import { AlertWords } from "./Notes";
import { NOTHING_TAKEN, OpenPackageCard } from "./OpenPackageCard";
import type { Offered } from "./offered";

export const LINE_GONE = msg`No longer in the catalogue.`;
export const REMOVE = msg`Remove`;
export const RESIZED_TO = msg`The package size has changed to {size} since this item was added. Enter the count again.`;
export const PACKAGE_COUNT = msg`{package} ({size} {unit})`;
export const PACKAGE_COUNT_RETIRED = msg`{package} ({size} {unit}), retired: taking only`;
export const LOOSE_UNITS = msg`Loose units`;
export const LOOSE_IN = msg`Loose {unit}`;
export const HOW_MANY = msg`How many`;
export const HOW_MANY_IN = msg`How many {unit}`;
export const IN_ALL = msg`{amount} {unit} in all`;

export function CartLine({
  line,
  item,
  opens,
  moving,
  onChange,
  onRemove,
}: {
  line: Line;
  item: Item | undefined;
  /** Its open packages where it comes from, to take from. */
  opens: Offered[];
  /** Whether an open package can move whole. */
  moving: boolean;
  onChange: (line: Line) => void;
  onRemove: () => void;
}) {
  if (item === undefined) {
    return (
      <li>
        {say(LINE_GONE)}{" "}
        <button type="button" onClick={onRemove}>
          {say(REMOVE)}
        </button>
      </li>
    );
  }
  const quantity = quantityOf(line, item);
  return (
    <li class="flex flex-col gap-2">
      <div>
        <strong>{item.name}</strong>
        {item.retired && <small> {say(RETIRED)}</small>}
      </div>
      {opens.map((open) => (
        <OpenPackageCard
          key={open.key}
          open={open}
          item={item}
          moving={moving}
          taken={line.open?.[open.key] ?? NOTHING_TAKEN}
          onTake={(next) =>
            onChange({ ...line, open: { ...line.open, [open.key]: next } })
          }
        />
      ))}
      {item.packages.map((pack) => {
        // The catalogue gives every size as a decimal, so never null.
        /* v8 ignore next */
        const size = decimal(thousandths(pack.size) ?? 0);
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
          item.unit === "each"
            ? say(item.packages.length > 0 ? LOOSE_UNITS : HOW_MANY)
            : say(item.packages.length > 0 ? LOOSE_IN : HOW_MANY_IN, {
                unit: item.unit,
              })
        }
      >
        {(id) => (
          <input
            id={id}
            inputMode={item.unit === "each" ? "numeric" : "decimal"}
            value={line.loose}
            onInput={(event) =>
              onChange({ ...line, loose: event.currentTarget.value })
            }
          />
        )}
      </Field>
      <div>
        {"problem" in quantity
          ? wordsOf(quantity.problem)
          : say(IN_ALL, { amount: decimal(quantity.amount), unit: item.unit })}
      </div>
      <button type="button" class="self-start" onClick={onRemove}>
        {say(REMOVE)}
      </button>
    </li>
  );
}
