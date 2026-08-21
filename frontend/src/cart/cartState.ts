/**
 * The multi-scan cart: one batch of stock movements, held in the browser until
 * the volunteer presses Save.
 *
 * What a cart is, why it is local, why the idempotency key is minted when the
 * cart opens rather than at submit, and why the key is scoped to the volunteer
 * who sent it are settled in docs/decisions/0011-qr-batch-scanning.md
 * (sections 1, 5 and 6). That the volunteer is a name picked from a list
 * rather than a credential is docs/decisions/0012-two-populations.md, point 5.
 *
 * Everything here is pure, so the values a reducer cannot invent -- the clock
 * reading behind the scan debounce, a fresh idempotency key -- arrive on the
 * action. `CartProvider` supplies them.
 */

/** `StockTransaction.Kind` on the backend. */
export type TransactionKind =
  | "checkout"
  | "checkin"
  | "receipt"
  | "consumption"
  | "transfer"
  | "adjustment"
  | "count";

/** The item fields a cart line displays; the catalogue read supplies them. */
export interface CartItem {
  id: number;
  name: string;
  unitOfMeasure: string;
}

/** A resolved label, as `GET /api/labels/{code}` returns it. */
export interface ScannedLabel {
  code: string;
  item: CartItem;
  /** What one scan of this label means. */
  quantity: number;
}

/** The label decode a line's quantity last came from, and when. */
export interface ScanStamp {
  code: string;
  at: number;
}

export interface CartLine {
  itemId: number;
  name: string;
  unitOfMeasure: string;
  quantity: number;
  /** Null on a line that was browsed or typed rather than scanned. */
  lastScan: ScanStamp | null;
}

export interface CartState {
  idempotencyKey: string;
  actorId: number | null;
  kind: TransactionKind;
  /** The location this batch moves stock from or to, per `kind`. */
  locationId: number | null;
  jobReference: string;
  lines: CartLine[];
}

/**
 * How long one label's decodes are treated as a single scan. A camera reads
 * the same code several times a second, and long enough that a second look at
 * the same packet is a decision rather than a frame.
 */
export const SCAN_DEBOUNCE_MS = 750;

/**
 * 128 bits of hex, well inside `StockTransaction.idempotency_key`. Not
 * `crypto.randomUUID`, which browsers expose only in a secure context: the
 * camera needs one but the rest of the cart does not, and a cart that cannot
 * be created is a worse failure than a scanner that will not open.
 */
export function mintIdempotencyKey(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function createCart(
  idempotencyKey: string = mintIdempotencyKey(),
): CartState {
  return {
    idempotencyKey,
    actorId: null,
    kind: "checkout",
    locationId: null,
    jobReference: "",
    lines: [],
  };
}

export type CartAction =
  | { type: "scan"; label: ScannedLabel; quantity?: number; at: number }
  | { type: "add"; item: CartItem; quantity: number }
  | { type: "setQuantity"; itemId: number; quantity: number }
  | { type: "remove"; itemId: number }
  | { type: "setActor"; actorId: number | null; idempotencyKey: string }
  | { type: "setKind"; kind: TransactionKind }
  | { type: "setLocation"; locationId: number | null }
  | { type: "setJobReference"; jobReference: string }
  | { type: "clear"; idempotencyKey: string };

/** One line per item, so a second scan of the same item adds to what is there. */
function upsertLine(
  lines: CartLine[],
  item: CartItem,
  quantity: number,
  lastScan: ScanStamp | null,
): CartLine[] {
  if (!lines.some((line) => line.itemId === item.id)) {
    return [
      ...lines,
      {
        itemId: item.id,
        name: item.name,
        unitOfMeasure: item.unitOfMeasure,
        quantity,
        lastScan,
      },
    ];
  }
  return lines.map((line) =>
    line.itemId === item.id
      ? { ...line, quantity: line.quantity + quantity, lastScan: lastScan ?? line.lastScan }
      : line,
  );
}

export function cartReducer(state: CartState, action: CartAction): CartState {
  switch (action.type) {
    case "scan": {
      const { label, at } = action;
      const lastScan = state.lines.find((line) => line.itemId === label.item.id)?.lastScan;
      // `elapsed >= 0` guards a clock that steps backwards -- NTP correcting
      // it, or the volunteer changing it. A negative difference is smaller
      // than the window, so without this every further scan of the code would
      // be swallowed until real time caught up, and silently: a debounced
      // scan returns unchanged state and shows the volunteer nothing.
      const elapsed = lastScan ? at - lastScan.at : Number.POSITIVE_INFINITY;
      if (lastScan?.code === label.code && elapsed >= 0 && elapsed < SCAN_DEBOUNCE_MS) {
        return state;
      }
      // The quantity override is the measured-item keypad: where the unit is
      // not `each` the volunteer enters the amount and the label's own
      // quantity is not a safe default.
      const quantity = action.quantity ?? label.quantity;
      return {
        ...state,
        lines: upsertLine(state.lines, label.item, quantity, { code: label.code, at }),
      };
    }
    case "add":
      return { ...state, lines: upsertLine(state.lines, action.item, action.quantity, null) };
    case "setQuantity":
      // A movement's quantity must be positive, so stepping a line to zero
      // takes it out of the cart rather than posting nothing.
      return {
        ...state,
        lines:
          action.quantity > 0
            ? state.lines.map((line) =>
                line.itemId === action.itemId ? { ...line, quantity: action.quantity } : line,
              )
            : state.lines.filter((line) => line.itemId !== action.itemId),
      };
    case "remove":
      return { ...state, lines: state.lines.filter((line) => line.itemId !== action.itemId) };
    case "setActor":
      // The server matches a retry on (actor, key), so a cart resubmitted
      // under a different volunteer is a batch it has not seen. Carrying the
      // old key into it would claim a retry of something that never happened.
      return action.actorId === state.actorId
        ? state
        : { ...state, actorId: action.actorId, idempotencyKey: action.idempotencyKey };
    case "setKind":
      return { ...state, kind: action.kind };
    case "setLocation":
      // Scanning a wall code repeatedly is harmless: setting it is idempotent,
      // which is why location scans need no debounce.
      return { ...state, locationId: action.locationId };
    case "setJobReference":
      return { ...state, jobReference: action.jobReference };
    case "clear":
      // The volunteer at the shelf is still the same person; the job the next
      // batch belongs to is not.
      return { ...createCart(action.idempotencyKey), actorId: state.actorId };
  }
}
