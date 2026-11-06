/**
 * A control with its words above it, close by; the form's gap spaces the
 * next field (styles.css). Field names the control: `children` makes it
 * with the id given, which the label is for. The words are bold unless
 * `quiet`.
 */
import type { ComponentChildren } from "preact";
import { useId } from "preact/hooks";

export function Field({
  label,
  quiet = false,
  children,
}: {
  label: ComponentChildren;
  quiet?: boolean;
  children: (id: string) => ComponentChildren;
}) {
  const id = useId();
  return (
    <div class="flex flex-col gap-1">
      <label for={id} class={quiet ? "font-normal" : undefined}>
        {label}
      </label>
      {children(id)}
    </div>
  );
}
