/**
 * The half a category and a location share: both nest inside one of their own.
 *
 * Stated once because it is one rule, the same way `TreeSerializer` states it
 * once on the server: a parent is optional, so a select over one always offers
 * "nothing", and a row is never offered as its own parent.
 */

/**
 * The value a `<select>` carries for "no parent".
 *
 * A sentinel string rather than an empty one, so it is told apart from a select
 * that has not been given a value at all — which React renders as uncontrolled
 * and says so loudly. It never reaches the API: the caller sends `null`.
 */
export const NO_PARENT = "none";

/** One nestable row, which is as much of either model as this needs. */
interface Nestable {
  id: number;
  name: string;
}

/** What a parent select is set to for a row that has one, or has not. */
export function parentValue(row: { parent: number | null } | null | undefined): string {
  return row?.parent === null || row?.parent === undefined ? NO_PARENT : String(row.parent);
}

/** And back: what the API is sent for whatever the select is holding. */
export function parentId(value: string): number | null {
  return value === NO_PARENT ? null : Number(value);
}

/**
 * What the parent select offers: nothing, then every row but this one.
 *
 * Excluding the row being edited is a convenience rather than the rule. The
 * rule is the `inventory_reject_tree_cycle` trigger, mirrored by
 * `TreeSerializer.validate_parent`, and it covers the loops this cannot see —
 * a grandparent chosen as a child. What this stops is the one loop somebody
 * would otherwise make by accident on the form in front of them.
 */
export function parentChoices(
  rows: Nestable[],
  itself?: number,
): { value: string; label: string }[] {
  return [
    { value: NO_PARENT, label: "Nothing — this is a top-level one" },
    ...rows
      .filter((row) => row.id !== itself)
      .map((row) => ({ value: String(row.id), label: row.name })),
  ];
}
