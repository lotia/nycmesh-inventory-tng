/** The alert asking for a reload once a newer build is out, and the
 * words on the status lines that link to its Upgrade button. */
import { msg } from "@lingui/core/macro";
import type { Signal } from "@preact/signals";
import {
  MUST_UPGRADE,
  MUST_UPGRADE_NOT_KEPT,
  UPDATED,
  UPGRADE,
} from "../shared/build";
import { say, sayParts } from "../shared/i18n";
import type { Outbox } from "../shared/outbox";
import { CAUTION_BOX } from "./Notes";

/** The Upgrade button's id, which the status lines link to while too
 * old (UpgradeToSave). */
export const UPGRADE_BUTTON = "upgrade";

export const UPGRADE_TO_SAVE = msg`Scanner updated. <0>Upgrade</0> to keep saving.`;

/**
 * Asks for a reload once a newer build is out (shared/build.ts). While this tab
 * can still save, only once the cart is saved or emptied and nothing waits
 * to send, as the newer build reads neither; until then it says nothing.
 * Too old, at once, saying what this tab holds is kept: the newer build
 * reads this build's cart, or lists it, and lists its saves waiting
 * (older-builds.ts); or, while the browser isn't keeping the cart, that
 * upgrading empties it (Ali, inventory-tng-sdfg). A newer
 * build's saves waiting here are not counted: that build sends them.
 */
export function NewerBuild({
  outbox,
  cartEmpty,
  cartKept,
  reload,
}: {
  outbox: Outbox;
  cartEmpty: Signal<boolean>;
  cartKept: Signal<boolean>;
  reload: () => void;
}) {
  const learned = outbox.builds.newer.value;
  const empty = cartEmpty.value;
  // Whether this tab holds what a newer build reads not as this build's:
  // a cart, or a batch waiting; too old, a newer build's own not counted.
  const holds =
    !empty ||
    outbox.entries.value.some(
      (entry) =>
        entry.state === "waiting" &&
        (learned !== "too_old" || !outbox.builds.isNewer(entry.build)),
    );
  if (learned === null || (learned !== "too_old" && holds)) {
    return null;
  }
  return (
    <div role="alert" class={CAUTION_BOX}>
      <div>{say(upgradeWords(holds, cartKept.value))}</div>
      <button
        type="button"
        id={UPGRADE_BUTTON}
        class="primary"
        onClick={reload}
      >
        {say(UPGRADE)}
      </button>
    </div>
  );
}

/** What the alert asking for a reload says. A reload keeps the cart
 * while the browser keeps it, an empty one included once none is stored
 * (Ali, inventory-tng-0y0u, inventory-tng-sdfg, inventory-tng-xm07). */
function upgradeWords(holds: boolean, reloadKeepsCart: boolean) {
  if (!reloadKeepsCart) {
    return MUST_UPGRADE_NOT_KEPT;
  }
  return holds ? MUST_UPGRADE : UPDATED;
}

/** What the camera's and the item search's status lines say while this
 * build is too old, as the alert saying so is below them: its Upgrade a
 * link that takes focus to the alert's Upgrade button (Ali, 2026-11-24,
 * inventory-tng-j3wt, inventory-tng-n1p8). A link, as it goes to another
 * place on the page and does nothing there itself. */
export function UpgradeToSave() {
  return (
    <>
      {sayParts(UPGRADE_TO_SAVE, {}, [
        (inside) => (
          <a href={`#${UPGRADE_BUTTON}`} onClick={toUpgrade}>
            {inside}
          </a>
        ),
      ])}
    </>
  );
}

/** Brings the alert's Upgrade button into view and takes focus to it,
 * scrolled as Back to camera does, as a phone's browser may not scroll to
 * what a script focuses; without it, the link's own jump. */
function toUpgrade(event: MouseEvent) {
  const button = document.getElementById(UPGRADE_BUTTON);
  if (button !== null) {
    event.preventDefault();
    button.scrollIntoView({ block: "center" });
    button.focus({ preventScroll: true });
  }
}
