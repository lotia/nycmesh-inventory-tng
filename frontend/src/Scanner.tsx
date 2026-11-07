/**
 * Scanning labels with the camera (inventory-tng-8i4.13.7): open until Done,
 * each label read once while it stays in view (labels.ts, Repeats), with a
 * word on what it did. A refused or missing camera says so; the lists below
 * still work.
 */
import { useSignal } from "@preact/signals";
import { useEffect, useRef } from "preact/hooks";
import { DecoderUnavailable, openCamera, type Session } from "./camera";
import { Repeats } from "./labels";

/** How often the camera's picture is read, in milliseconds. */
const READ_EVERY_MS = 200;

export function Scanner({
  onCode,
  onDone,
  open = openCamera,
}: {
  /** What a code read did, in words for the volunteer. */
  onCode: (code: string) => string;
  onDone: () => void;
  open?: (video: HTMLVideoElement) => Promise<Session>;
}) {
  const video = useRef<HTMLVideoElement>(null);
  // The newest onCode, so a refreshed catalogue is read from at once.
  const handle = useRef(onCode);
  handle.current = onCode;
  const said = useSignal("Starting the camera…");
  const torch = useSignal<((on: boolean) => Promise<void>) | null>(null);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const repeats = new Repeats();
    const camera = open(video.current as HTMLVideoElement);
    camera.then(
      (session) => {
        if (stopped) {
          return;
        }
        torch.value = session.torch;
        said.value = "Point the camera at a label.";
        // Each read waits for the one before: a slow phone never piles
        // them up.
        const read = async () => {
          try {
            const codes = await session.read();
            // Closed while reading: what it read is not taken.
            for (const code of stopped ? [] : codes) {
              if (repeats.fresh(code, Date.now())) {
                said.value = handle.current(code);
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
        said.value = refused(error);
      },
    );
    return () => {
      stopped = true;
      clearTimeout(timer);
      // Stopped when it opens, if it has not yet.
      camera.then(
        (session) => session.stop(),
        () => {},
      );
    };
  }, []);

  return (
    <section aria-label="Camera">
      <video
        class="block max-h-[60vh] w-full rounded bg-black object-cover"
        ref={video}
        muted
        playsInline
      />
      <p role="status">{said.value}</p>
      <div class="actions">
        {torch.value !== null && (
          <Torch
            turn={torch.value}
            onFail={() => {
              said.value = "The torch wouldn't switch.";
            }}
          />
        )}
        <button type="button" onClick={onDone}>
          Done
        </button>
      </div>
    </section>
  );
}

/** Why the camera could not be used, in words. */
function refused(error: unknown): string {
  if (error instanceof DecoderUnavailable) {
    return "The label reader didn't load. Check the connection, then press Done and try again.";
  }
  return error instanceof DOMException && error.name === "NotAllowedError"
    ? "The camera isn't allowed for this page. Allow it in the browser's settings, or find items in the list."
    : "No camera could be opened. Find items in the list instead.";
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
      Torch
    </button>
  );
}
