import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { WORKING_EDGE } from "../src/scan/frame";
import { manage } from "./django";

/**
 * A camera that is really pointing at one of this project's own labels.
 *
 * Chromium accepts `--use-file-for-fake-video-capture=<file>.y4m` beside the
 * fake-device flags in camera.ts, and feeds that file
 * to `getUserMedia` in place of a lens. What that buys, and what it is for, is
 * in the header of decodes.spec.ts; this file is only how the clip is made.
 *
 * WHAT IS DRAWN IS THE PROJECT'S OWN SYMBOL, not an invention of this file.
 * The matrix comes from `inventory.labels.symbol_for`, which is what the
 * printed sticker is drawn from, at the error correction level and in the
 * encoding mode that module fixes. A QR generated here at level L would decode
 * more easily and prove less.
 *
 * NO ffmpeg AND NO CONTAINER. Y4M is uncompressed frames behind a one-line
 * header, so writing it is arithmetic and Node can do it -- which removes the
 * whole question of whether a container runtime is available to the suite,
 * and of what uid a rootless Podman bind mount writes as. It also means the
 * clip is generated during the run rather than committed, which it has to be:
 * one frame at this size is half a megabyte, and a committed one would go
 * stale against whatever the label generator produces next.
 */

/**
 * The frame size, and it is chosen rather than arbitrary.
 *
 * The width is `WORKING_EDGE` itself, taken from the module that bounds it:
 * scan/frame.ts scales only what is longer, so a frame exactly that wide
 * reaches the decoder as it was drawn. Testing the downscale as well is worth
 * doing one day, but a failure would then not say whether the handoff or the
 * downscale was at fault. The height is the short edge of 4:3.
 */
const WIDTH = WORKING_EDGE;
const HEIGHT = 480;

/**
 * Frames per second the header declares, and how many frames follow it.
 *
 * One frame. Chromium loops the file, and every frame of this clip would be
 * the same picture anyway -- a sticker held up to a lens does not move, and a
 * still removes motion blur as an explanation for a failure.
 */
const FRAME_RATE = 15;
const FRAMES = 1;

/**
 * Limited-range luma, which is what Y4M's C420 means without a colour range
 * tag: 16 is black and 235 is white. Full-range values would be clamped on the
 * way to RGB and cost the decoder contrast it has no reason to spend.
 */
const BLACK = 16;
const WHITE = 235;
/** Neutral chroma. The label is black on white, so there is no colour in it. */
const GREY = 128;

/**
 * How much of the frame the symbol fills.
 *
 * A label at arm's length is a small thing in a big picture, and filling the
 * frame would prove the decoder can read a symbol nobody will ever present it
 * with. 60% of the short edge is a sticker held up to a phone, and clears
 * MINIMUM_MODULE_PX below with room to spare.
 */
const FILL = 0.6;

/**
 * The floor under the drawn module, in pixels, and why it is stated.
 *
 * `inventory.labels` has the same guard in millimetres (`MINIMUM_MODULE_MM`)
 * for the same reason: the symbol grows a version when `LABEL_BASE_URL` grows,
 * and at a fixed fill that is the module shrinking. Three pixels a module is
 * the floor a QR decodes at, which is the number `WORKING_EDGE` in
 * scan/frame.ts is sized from. Under it the clip is unreadable and this test
 * fails thirty seconds later as a cart line that never appeared -- blaming the
 * app for the fixture.
 */
const MINIMUM_MODULE_PX = 3;

export interface PrintedLabel {
  /** The code on the sticker. Names the clip, and is there to be asserted on. */
  code: string;
  /** The symbol's dark modules, without its quiet zone. */
  matrix: number[][];
  /** The quiet zone the standard requires, in modules. */
  quiet: number;
}

/**
 * Mint a label against a seeded item, and answer with the symbol it prints as.
 *
 * Through Django rather than through the API: the API would need a session
 * before the browser this clip is *for* has been launched, and what is being
 * exercised here is the scanner, not the write path that api-reachable.spec.ts
 * already covers. What matters is that the code is real, resolvable, and
 * carries a quantity the batch can be checked against.
 *
 * The item is named by its id, which global setup published: the scene belongs
 * to seed_integration_data and this suite does not keep its own copy of it.
 */
export function mintLabel(quantity: number, item: number): PrintedLabel {
  const printed = manage(
    "shell",
    "-c",
    // One expression per line, and every constant read from the application
    // rather than repeated here: the quiet zone and the encoding are
    // decisions inventory/labels.py owns.
    [
      "import json",
      "from inventory.labels import QUIET_ZONE_MODULES, symbol_for",
      "from inventory.models import Item, Label",
      `item = Item.objects.get(pk=${item})`,
      `label = Label.objects.create(code=Label.mint_unique_code(), item=item, quantity=${quantity})`,
      "matrix = [[1 if dark else 0 for dark in row] for row in symbol_for(label.code).matrix]",
      'print(json.dumps({"code": label.code, "matrix": matrix, "quiet": QUIET_ZONE_MODULES}))',
    ].join("\n"),
  );
  // The last line: `manage.py shell -c` is free to say other things first.
  const said = printed.trim().split("\n");
  return JSON.parse(said[said.length - 1]) as PrintedLabel;
}

/** The luma plane for one frame: a white field with the symbol drawn on it. */
function luma({ matrix, quiet }: PrintedLabel): Uint8Array {
  const plane = new Uint8Array(WIDTH * HEIGHT).fill(WHITE);
  const across = matrix.length + 2 * quiet;
  // Whole pixels per module, so no module is drawn a pixel wider than its
  // neighbour: an uneven grid is a decode failure that would look like a bug
  // in the code under test.
  const module = Math.floor((Math.min(WIDTH, HEIGHT) * FILL) / across);
  if (module < MINIMUM_MODULE_PX) {
    throw new Error(
      `A ${across}-module symbol filling ${FILL} of a ${WIDTH}x${HEIGHT} frame is ${module} ` +
        `pixels per module, below the ${MINIMUM_MODULE_PX} this clip needs to be readable. ` +
        "LABEL_BASE_URL has grown the symbol a version: raise FILL, or the frame size with it.",
    );
  }
  const left = Math.floor((WIDTH - across * module) / 2);
  const top = Math.floor((HEIGHT - across * module) / 2);
  for (const [row, modules] of matrix.entries()) {
    for (const [column, dark] of modules.entries()) {
      if (dark === 0) {
        continue;
      }
      const x = left + (column + quiet) * module;
      const y = top + (row + quiet) * module;
      for (let line = y; line < y + module; line += 1) {
        plane.fill(BLACK, line * WIDTH + x, line * WIDTH + x + module);
      }
    }
  }
  return plane;
}

/**
 * Write the clip, and answer with its path and the way to delete it.
 *
 * A still, which is what a volunteer holding a sticker up to a phone looks
 * like, and which removes motion blur as an explanation for a failure. What a
 * sticker that never leaves the frame does to the batch is decodes.spec.ts's
 * to say, because it is what has to assert around it.
 */
export function filmed(label: PrintedLabel): { path: string; remove: () => void } {
  const directory = mkdtempSync(join(tmpdir(), "inventory-tng-scan-"));
  const path = join(directory, `${label.code}.y4m`);
  const plane = luma(label);
  // Half resolution in both directions, which is what C420 means, and one
  // value throughout: nothing on a label is coloured.
  const chroma = new Uint8Array((WIDTH / 2) * (HEIGHT / 2)).fill(GREY);
  const frame = Buffer.concat([Buffer.from("FRAME\n"), plane, chroma, chroma]);
  writeFileSync(
    path,
    Buffer.concat([
      Buffer.from(`YUV4MPEG2 W${WIDTH} H${HEIGHT} F${FRAME_RATE}:1 Ip A1:1 C420\n`),
      ...Array.from({ length: FRAMES }, () => frame),
    ]),
  );
  return {
    path,
    remove: () => {
      rmSync(directory, { recursive: true, force: true });
    },
  };
}
