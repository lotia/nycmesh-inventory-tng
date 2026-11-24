/**
 * Scanning labels with the camera (inventory-tng-8i4.13.7): open until Done,
 * each label read once while it stays in view (repeated-reads.ts, Repeats),
 * with a word on what it did. A refused or missing camera says so, and
 * offers Reload page where that may help, with no Done (Ali,
 * inventory-tng-qy02), saying to record the cart first while a reload
 * or restart would empty it (Ali, inventory-tng-0y0u, inventory-tng-xtig);
 * the lists below still work.
 * No label is read while the picture is not wholly on the screen, so
 * nothing is added out of the volunteer's sight (Ali, 2026-11-24,
 * inventory-tng-rj2g), nor while a text box has focus, as the on-screen
 * keyboard may cover the picture (Ali, 2026-11-24, inventory-tng-5663),
 * nor while this build is too old, till the volunteer upgrades (Ali,
 * 2026-11-24, inventory-tng-af63);
 * a label held in view through the pause is not taken again
 * (repeated-reads.ts). Off the screen, the status line keeps its last
 * words; while a text box has focus, it says reading waits, and a tap on
 * the picture takes focus from the box (Ali, 2026-11-24,
 * inventory-tng-0w3n). Once reading resumes, it says a refusal or question
 * still standing again, or else to point the camera at a label, so what a
 * label did is not announced twice (inventory-tng-3pxj). A place's
 * question answered says what the answer did; put away another way, to
 * point the camera at a label (Ali, 2026-11-24, inventory-tng-8ary).
 * While this build is too old, it says to upgrade, over everything else
 * (NewerBuild's UpgradeToSave; Ali, 2026-11-24, inventory-tng-j3wt).
 * Each session is reported (camera-report.ts).
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import {
  effect,
  type ReadonlySignal,
  useComputed,
  useSignal,
} from "@preact/signals";
import { useEffect, useRef } from "preact/hooks";
import { say } from "../shared/i18n";
import { openCamera, type Session } from "./camera";
import {
  CameraReport,
  type LabelKind,
  outcomeOf,
  type Refused,
} from "./camera-report";
import type { Cart } from "./cart";
import { UpgradeToSave } from "./NewerBuild";
import { AlertWords } from "./Notes";
import { type About, useClearedWhenLineChanges } from "./refused-line";
import { Repeats } from "./repeated-reads";
import { isTextBox } from "./text-box-focus";

export const CAMERA = msg`Camera`;
export const STARTING = msg`Starting the camera…`;
export const POINT = msg`Point the camera at a label.`;
export const TYPING = msg`Not reading while you type. Tap the picture to scan.`;
export const TORCH_FAILED = msg`The torch wouldn't switch.`;
export const DONE = msg`Done`;
export const TORCH = msg`Torch`;

export const RELOAD_PAGE = msg`Reload page`;

/** Why the camera could not be used, in the cart alerts' form: a bold
 * heading, then a plain hint at what to do, and whether Reload page is
 * offered, as reloading may help (Ali, inventory-tng-qy02). One whose
 * hint says to reload or restart has a hint for when the browser isn't
 * keeping the cart, as either then empties it (Ali, inventory-tng-0y0u,
 * inventory-tng-xtig); one offering Reload page must have it. */
export type Refusal = {
  bold: MessageDescriptor;
  plain: MessageDescriptor;
} & (
  | { reload: true; notKept: MessageDescriptor }
  | { reload: false; notKept?: MessageDescriptor }
);

export const NOT_ALLOWED = {
  bold: msg`The camera isn't allowed for this page.`,
  plain: msg`Allow it in the browser's settings, then reload the page, or find items in the list.`,
  reload: true,
  notKept: msg`Allow it in the browser's settings. This browser isn't keeping the cart, so record it before you reload the page, or find items in the list.`,
} satisfies Refusal;
export const IN_USE = {
  bold: msg`The camera is in use.`,
  plain: msg`Close the other app using it, then reload the page, or find items in the list.`,
  reload: true,
  notKept: msg`Close the other app using it. This browser isn't keeping the cart, so record it before you reload the page, or find items in the list.`,
} satisfies Refusal;
export const NO_DECODER = {
  bold: msg`The label reader didn't load.`,
  plain: msg`Check the connection, then reload the page.`,
  reload: true,
  notKept: msg`Check the connection. This browser isn't keeping the cart, so record it before you reload the page.`,
} satisfies Refusal;
export const NO_CAMERA = {
  bold: msg`No camera could be found.`,
  plain: msg`Check the device's settings allow this browser to use the camera, then restart the browser, or find items in the list.`,
  reload: false,
  notKept: msg`Check the device's settings allow this browser to use the camera. This browser isn't keeping the cart, so record it before you restart the browser, or find items in the list.`,
} satisfies Refusal;
export const NOT_OPENED = {
  bold: msg`The camera couldn't be opened.`,
  plain: msg`Reload the page to try again, or find items in the list.`,
  reload: true,
  notKept: msg`This browser isn't keeping the cart, so record it before you reload the page, or find items in the list.`,
} satisfies Refusal;
export const INSECURE = {
  bold: msg`The camera needs a secure connection.`,
  plain: msg`Ask an administrator for the page's https address, or find items in the list.`,
  reload: false,
} satisfies Refusal;

/** What the volunteer is told for each way the report says the camera
 * was refused, so the two never disagree. */
const REFUSALS: Record<Refused, Refusal> = {
  not_allowed: NOT_ALLOWED,
  in_use: IN_USE,
  decoder_failed: NO_DECODER,
  not_found: NO_CAMERA,
  other: NOT_OPENED,
  insecure: INSECURE,
};

/** What a code read did, in words for the volunteer, what its label
 * named, for the report, refused, the cart line it is about, if any
 * (refused-line.ts), and, for a refusal or question, that its words still
 * stand, so they are said again once typing ends: a refusal's (true) till
 * the next read or a change to its line; a question's while this gives
 * null, then the words it gives instead, once it is answered or put
 * away. */
export type Acted = {
  said: string;
  label: LabelKind;
  about?: About | undefined;
  stands?: true | ReadonlySignal<MessageDescriptor | string | null> | undefined;
};

/** How often the camera's picture is read, in milliseconds. */
export const READ_EVERY_MS = 200;

export function Scanner({
  cart,
  onCode,
  onDone,
  onPicture,
  onScreen,
  typing,
  tooOld,
  reload,
  reloadKeepsCart,
  open = openCamera,
}: {
  /** The cart as it is now, whose lines a refusal may be about. */
  cart: Cart;
  onCode: (code: string) => Acted;
  onDone: () => void;
  /** Given the camera's picture once it shows, and null once it stops
   * (BackToCamera). */
  onPicture: (video: HTMLVideoElement | null) => void;
  /** Whether the picture is wholly on the screen (useWhollyOnScreen). */
  onScreen: ReadonlySignal<boolean>;
  /** Whether a text box has focus (useTextBoxFocused). */
  typing: ReadonlySignal<boolean>;
  /** Whether the server takes nothing more from this build (NewerBuild). */
  tooOld: ReadonlySignal<boolean>;
  /** Refresh this page (ScanPage's reload). */
  reload: () => void;
  /** Whether a reload or restart would bring back the cart
   * (CartStore.write). */
  reloadKeepsCart: boolean;
  open?: (video: HTMLVideoElement) => Promise<Session>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const reloadButton = useRef<HTMLButtonElement>(null);
  const refusalWords = useRef<HTMLParagraphElement>(null);
  // The newest onCode, so a refreshed catalogue is read from at once.
  const handle = useRef(onCode);
  handle.current = onCode;
  // The one place that decides whether labels are read: only while the
  // picture is wholly on the screen, no text box has focus and this build
  // is not too old.
  const reads = useComputed(
    () => onScreen.value && !typing.value && !tooOld.value,
  );
  // A phrase, or said as shown: the words onCode said; once the line a
  // refusal was about changes, empty (typing's end makes that Point).
  const said = useSignal<MessageDescriptor | string>(STARTING);
  // Set while `said` still stands: true for a refusal; for a question,
  // what stops watching what became of it.
  const standing = useRef<true | (() => void) | undefined>(undefined);
  const unwatch = () => {
    if (typeof standing.current === "function") {
      standing.current();
    }
    standing.current = undefined;
  };
  const refusing = useClearedWhenLineChanges(cart, () => {
    unwatch();
    said.value = "";
  });
  const show = (
    words: MessageDescriptor | string,
    { about, stands }: Pick<Acted, "about" | "stands"> = {},
  ) => {
    unwatch();
    said.value = words;
    refusing(about);
    standing.current =
      stands === undefined || stands === true
        ? stands
        : stands.subscribe((instead) => {
            if (instead !== null) {
              show(instead);
            }
          });
  };
  useEffect(() => unwatch, []);
  // Why the camera could not be used, once it was refused.
  const refused = useSignal<Refusal | null>(null);
  const torch = useSignal<((on: boolean) => Promise<void>) | null>(null);
  const refusal = refused.value;
  // As typing starts and ends, anything but a refusal or question still
  // standing becomes Point, so the live region does not announce what a
  // label did, or a question answered meanwhile, once typing ends;
  // Starting stays till the camera opens. Done as `typing` changes, so
  // before the line is drawn again.
  useEffect(
    () =>
      effect(() => {
        typing.value;
        if (standing.current === undefined && said.peek() !== STARTING) {
          said.value = POINT;
        }
      }),
    [],
  );

  // Done went with the refusal: Reload page takes the focus, or, where
  // none is offered, the refusal's words, so the volunteer keeps their
  // place; unless something else on the page holds it.
  useEffect(() => {
    if (document.activeElement === document.body) {
      (reloadButton.current ?? refusalWords.current)?.focus();
    }
  }, [refusal]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const repeats = new Repeats();
    const report = new CameraReport();
    const camera = open(video.current as HTMLVideoElement);
    camera.then(
      (session) => {
        report.opened(session.decoder);
        // Opened while hidden: sent now, as the page may not come back.
        if (document.visibilityState === "hidden") {
          report.hidden();
        }
        if (stopped) {
          return;
        }
        torch.value = session.torch;
        show(POINT);
        onPicture(video.current);
        // When reading paused (reads).
        let pausedAt: number | null = null;
        // Each read waits for the one before: a slow phone never piles
        // them up.
        const read = async () => {
          try {
            if (pausedAt !== null && reads.peek()) {
              repeats.paused(Date.now() - pausedAt);
              pausedAt = null;
            }
            const asked = Date.now();
            const codes = reads.peek() ? await session.read() : [];
            if (!reads.peek()) {
              // Paused, or paused while reading: nothing read is taken,
              // and the pause counts from this read, so a slow one thrown
              // away is not left out of it.
              pausedAt ??= asked;
            }
            // Closed while reading: what it read is not taken.
            const taken = stopped || pausedAt !== null ? [] : codes;
            for (const code of taken) {
              if (repeats.fresh(code, Date.now())) {
                const acted = handle.current(code);
                report.read(acted.label);
                show(acted.said, acted);
              }
            }
          } catch {
            // A frame that cannot be read yet, as the camera starts.
          }
          if (!stopped) {
            timer = setTimeout(read, READ_EVERY_MS);
          }
        };
        timer = setTimeout(read, READ_EVERY_MS);
      },
      (error: unknown) => {
        // Done before it opened: the browser may say the picture's play
        // was aborted, which is not the camera refused.
        if (stopped) {
          report.cancelled();
          return;
        }
        const outcome = outcomeOf(error);
        report.refused(outcome);
        refused.value = REFUSALS[outcome];
      },
    );
    // Reported as the page is hidden or left, which may be the last of it.
    const listening = new AbortController();
    const { signal } = listening;
    document.addEventListener(
      "visibilitychange",
      () =>
        document.visibilityState === "hidden"
          ? report.hidden()
          : report.shown(),
      { signal },
    );
    window.addEventListener("pagehide", () => report.hidden(), { signal });
    return () => {
      stopped = true;
      clearTimeout(timer);
      onPicture(null);
      listening.abort();
      // Stopped when it opens, if it has not yet, and reported.
      camera.then(
        (session) => {
          session.stop();
          report.closed();
        },
        () => {},
      );
    };
  }, []);

  return (
    // Focusable, so Back to camera can hand focus here as it goes.
    <section aria-label={say(CAMERA)} tabIndex={-1}>
      {/* Refused, it shows nothing: hidden, as no Done takes it away. */}
      <video
        class="block max-h-[60vh] w-full rounded bg-black object-cover"
        ref={video}
        hidden={refusal !== null}
        muted
        playsInline
        onClick={() => {
          // The keyboard may be gone with focus still in its box.
          const box = document.activeElement;
          if (box instanceof HTMLElement && isTextBox(box)) {
            box.blur();
          }
        }}
      />
      {/* A refusal is a problem, in the alert's style (CONTRIBUTING,
          Styles); a new element, so it is announced as it appears. */}
      {refusal === null ? (
        <>
          <p role="status" class="status-line">
            {/* In this order: too old, typing, then the words beneath. */}
            {tooOld.value ? (
              <UpgradeToSave />
            ) : (
              wordsOf(typing.value ? TYPING : said.value)
            )}
          </p>
          <div class="actions">
            {torch.value !== null && (
              <Torch turn={torch.value} onFail={() => show(TORCH_FAILED)} />
            )}
            <button type="button" onClick={onDone}>
              {say(DONE)}
            </button>
          </div>
        </>
      ) : (
        <>
          <p role="alert" ref={refusalWords} tabIndex={-1}>
            <AlertWords
              note={{
                bold: [say(refusal.bold)],
                plain: [" ", say(hintOf(refusal, reloadKeepsCart))],
              }}
            />
          </p>
          {refusal.reload && (
            <div class="actions">
              <button type="button" ref={reloadButton} onClick={reload}>
                {say(RELOAD_PAGE)}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** A phrase in the volunteer's language, or words already said so. */
function wordsOf(words: MessageDescriptor | string) {
  return typeof words === "string" ? words : say(words);
}

/** A refusal's hint: to record the cart first where reloading or
 * restarting would empty it. */
function hintOf(refusal: Refusal, reloadKeepsCart: boolean) {
  return (!reloadKeepsCart && refusal.notKept) || refusal.plain;
}

/** Turns the camera's torch on and off. */
function Torch({
  turn,
  onFail,
}: {
  turn: (on: boolean) => Promise<void>;
  onFail: () => void;
}) {
  const lit = useSignal(false);
  return (
    <button
      type="button"
      aria-pressed={lit.value}
      onClick={async () => {
        try {
          await turn(!lit.value);
          lit.value = !lit.value;
        } catch {
          onFail();
        }
      }}
    >
      {say(TORCH)}
    </button>
  );
}
