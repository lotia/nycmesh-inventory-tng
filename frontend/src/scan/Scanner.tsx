/**
 * Scanning labels with the camera (inventory-tng-8i4.13.7): open until Done,
 * each label read once while it stays in view (repeated-reads.ts, Repeats),
 * with a word on what it did. A refused or missing camera says so, and
 * offers Reload page where that may help, with no Done (Ali,
 * inventory-tng-qy02); the lists below still work. Each session is
 * reported (camera-report.ts).
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { useSignal } from "@preact/signals";
import { useEffect, useRef } from "preact/hooks";
import { say } from "../shared/i18n";
import { openCamera, type Session } from "./camera";
import {
  CameraReport,
  type LabelKind,
  outcomeOf,
  type Refused,
} from "./camera-report";
import { AlertWords } from "./Notes";
import { Repeats } from "./repeated-reads";

export const CAMERA = msg`Camera`;
export const STARTING = msg`Starting the camera…`;
export const POINT = msg`Point the camera at a label.`;
export const TORCH_FAILED = msg`The torch wouldn't switch.`;
export const DONE = msg`Done`;
export const TORCH = msg`Torch`;

export const RELOAD_PAGE = msg`Reload page`;

/** Why the camera could not be used, in the cart alerts' form: a bold
 * heading, then a plain hint at what to do, and whether Reload page is
 * offered, as reloading may help (Ali, inventory-tng-qy02). */
export type Refusal = {
  bold: MessageDescriptor;
  plain: MessageDescriptor;
  reload: boolean;
};

export const NOT_ALLOWED: Refusal = {
  bold: msg`The camera isn't allowed for this page.`,
  plain: msg`Allow it in the browser's settings, then reload the page, or find items in the list.`,
  reload: true,
};
export const IN_USE: Refusal = {
  bold: msg`The camera is in use.`,
  plain: msg`Close the other app using it, then reload the page, or find items in the list.`,
  reload: true,
};
export const NO_DECODER: Refusal = {
  bold: msg`The label reader didn't load.`,
  plain: msg`Check the connection, then reload the page.`,
  reload: true,
};
export const NO_CAMERA: Refusal = {
  bold: msg`No camera could be found.`,
  plain: msg`Check the phone's settings allow this browser to use the camera, then restart the browser, or find items in the list.`,
  reload: false,
};
export const INSECURE: Refusal = {
  bold: msg`The camera needs a secure connection.`,
  plain: msg`Ask an administrator for the page's https address, or find items in the list.`,
  reload: false,
};

/** What the volunteer is told for each way the report says the camera
 * was refused, so the two never disagree. */
const REFUSALS: Record<Refused, Refusal> = {
  not_allowed: NOT_ALLOWED,
  in_use: IN_USE,
  decoder_failed: NO_DECODER,
  not_found: NO_CAMERA,
  other: NO_CAMERA,
  insecure: INSECURE,
};

/** What a code read did, in words for the volunteer, and what its label
 * named, for the report. */
export type Acted = { said: string; label: LabelKind };

/** How often the camera's picture is read, in milliseconds. */
export const READ_EVERY_MS = 200;

export function Scanner({
  onCode,
  onDone,
  reload,
  open = openCamera,
}: {
  onCode: (code: string) => Acted;
  onDone: () => void;
  /** Refresh this page (ScanPage's reload). */
  reload: () => void;
  open?: (video: HTMLVideoElement) => Promise<Session>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const reloadButton = useRef<HTMLButtonElement>(null);
  // The newest onCode, so a refreshed catalogue is read from at once.
  const handle = useRef(onCode);
  handle.current = onCode;
  // A phrase, said as shown, the words onCode said, or why the camera
  // could not be used.
  const said = useSignal<MessageDescriptor | string | Refusal>(STARTING);
  const torch = useSignal<((on: boolean) => Promise<void>) | null>(null);
  const refusal = isRefusal(said.value) ? said.value : null;

  // Done went with the refusal: Reload page takes the focus, unless
  // something else on the page holds it.
  useEffect(() => {
    if (document.activeElement === document.body) {
      reloadButton.current?.focus();
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
        said.value = POINT;
        // Each read waits for the one before: a slow phone never piles
        // them up.
        const read = async () => {
          try {
            const codes = await session.read();
            // Closed while reading: what it read is not taken.
            for (const code of stopped ? [] : codes) {
              if (repeats.fresh(code, Date.now())) {
                const acted = handle.current(code);
                report.read(acted.label);
                said.value = acted.said;
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
        said.value = REFUSALS[outcome];
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
    <section aria-label={say(CAMERA)}>
      <video
        class="block max-h-[60vh] w-full rounded bg-black object-cover"
        ref={video}
        muted
        playsInline
      />
      <p role="status">
        <Said said={said.value} />
      </p>
      {refusal === null ? (
        <div class="actions">
          {torch.value !== null && (
            <Torch
              turn={torch.value}
              onFail={() => {
                said.value = TORCH_FAILED;
              }}
            />
          )}
          <button type="button" onClick={onDone}>
            {say(DONE)}
          </button>
        </div>
      ) : (
        refusal.reload && (
          <div class="actions">
            <button type="button" ref={reloadButton} onClick={reload}>
              {say(RELOAD_PAGE)}
            </button>
          </div>
        )
      )}
    </section>
  );
}

function isRefusal(
  said: MessageDescriptor | string | Refusal,
): said is Refusal {
  return typeof said !== "string" && "bold" in said;
}

/** What the camera said last: a refusal as a bold heading then its hint. */
function Said({ said }: { said: MessageDescriptor | string | Refusal }) {
  if (typeof said === "string") {
    return <>{said}</>;
  }
  if (isRefusal(said)) {
    return (
      <AlertWords
        note={{ bold: [say(said.bold)], plain: [" ", say(said.plain)] }}
      />
    );
  }
  return <>{say(said)}</>;
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
