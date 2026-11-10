/**
 * A list's search box, suggesting as one types (docs/decisions/0019): an
 * ARIA combobox with a listbox of the closest records, from the address
 * in its `data-suggest` (backend admin/listing.py). Arrows move through
 * them, Enter or a tap goes to the one picked, Escape closes them. Enter
 * with none picked sends the form, which lists every close match, as it
 * does without this script.
 */

/** A suggestion, as the backend sends it. */
export interface Suggestion {
  name: string;
  hint: string;
  href: string;
}

export interface Options {
  /** How long typing must pause before asking, in milliseconds. */
  wait?: number;
  fetch?: typeof fetch;
  go?: (href: string) => void;
}

let made = 0;

/** Makes `input` a combobox suggesting from its `data-suggest` address. */
export function suggest(
  input: HTMLInputElement,
  {
    wait = 150,
    fetch: get = fetch,
    go = (href) => location.assign(href),
  }: Options = {},
): void {
  const document = input.ownerDocument;
  const id = `suggestions-${++made}`;
  const list = document.createElement("ul");
  list.id = id;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", "Suggestions");
  list.className =
    "absolute inset-x-0 top-full z-10 m-0 list-none rounded border border-line bg-page p-0 font-normal shadow-md";
  list.hidden = true;
  input.after(list);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", id);
  input.setAttribute("aria-expanded", "false");

  let shown: Suggestion[] = [];
  let active = -1;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let asking: AbortController | undefined;

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }

  function mark(index: number) {
    active = index;
    for (const [at, option] of [...list.children].entries()) {
      option.setAttribute("aria-selected", String(at === index));
      option.classList.toggle("bg-highlight", at === index);
    }
    if (index < 0) input.removeAttribute("aria-activedescendant");
    else input.setAttribute("aria-activedescendant", `${id}-${index}`);
  }

  function show(found: Suggestion[]) {
    shown = found;
    list.replaceChildren(
      ...found.map((each, at) => {
        const option = document.createElement("li");
        option.id = `${id}-${at}`;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", "false");
        option.className =
          "flex min-h-11 cursor-pointer flex-col justify-center px-2 py-1 hover:bg-highlight";
        option.append(each.name);
        if (each.hint) {
          const hint = document.createElement("span");
          hint.className = "hint";
          hint.textContent = each.hint;
          option.append(hint);
        }
        // Keep focus in the box, so it stays a combobox, then go.
        option.addEventListener("mousedown", (event) => event.preventDefault());
        option.addEventListener("click", () => go(each.href));
        return option;
      }),
    );
    active = -1;
    if (found.length === 0) {
      close();
      return;
    }
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  async function ask(typed: string) {
    asking?.abort();
    if (!typed.trim()) {
      show([]);
      return;
    }
    asking = new AbortController();
    const url = new URL(input.dataset.suggest ?? "", location.href);
    url.searchParams.set("q", typed);
    try {
      const answer = await get(url, { signal: asking.signal });
      if (answer.ok) show((await answer.json()) as Suggestion[]);
    } catch {
      // Aborted by newer typing, or offline: the form still searches.
    }
  }

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => ask(input.value), wait);
  });

  input.addEventListener("keydown", (event) => {
    const open = !list.hidden;
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        if (shown.length === 0) return;
        event.preventDefault();
        if (!open) {
          list.hidden = false;
          input.setAttribute("aria-expanded", "true");
        }
        // Past either end, back to the box itself (-1), then round again.
        const last = shown.length - 1;
        if (event.key === "ArrowDown") mark(active === last ? -1 : active + 1);
        else mark(active === -1 ? last : active - 1);
        return;
      }
      case "Enter": {
        const picked = open ? shown[active] : undefined;
        if (picked) {
          event.preventDefault();
          go(picked.href);
        }
        return;
      }
      case "Escape":
        if (open) {
          event.preventDefault();
          close();
        }
        return;
    }
  });

  input.addEventListener("blur", close);
}
