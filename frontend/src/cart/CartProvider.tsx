/**
 * The cart is shared by the scanner, the item list, the volunteer picker and
 * the submit bar, so it is one `useReducer` behind a context and no state
 * library. See docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from "react";
import { type CartAction, type CartState, cartReducer, mintIdempotencyKey } from "./cartState";
import { loadCart, saveCart } from "./cartStorage";

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/**
 * What a component dispatches: an action without the clock reading or the
 * fresh key, which the provider fills in so the reducer stays pure.
 */
export type CartIntent = DistributiveOmit<CartAction, "at" | "idempotencyKey">;

export interface CartContextValue {
  cart: CartState;
  dispatch: (intent: CartIntent) => void;
  /**
   * Give this batch up: clear the cart and put the empty one on the device in
   * the same breath.
   *
   * `dispatch({ type: "clear" })` would leave the writing to the effect below,
   * which runs after the render -- and the caller that gives a batch away has
   * already stored it somewhere else by then. A reload landing in between
   * would restore a cart holding those lines under the key the outbox is about
   * to send them with, so editing and saving it would be matched as a replay
   * and the corrections dropped without a word. Both writes happen here, in
   * one turn, where nothing can land between them.
   */
  handOver: () => void;
}

const CartContext = createContext<CartContextValue | null>(null);

function toAction(intent: CartIntent): CartAction {
  switch (intent.type) {
    case "scan":
      return { ...intent, at: Date.now() };
    case "setActor":
      return { ...intent, idempotencyKey: mintIdempotencyKey() };
    case "clear":
      return {
        ...intent,
        idempotencyKey: mintIdempotencyKey(),
      };
    default:
      return intent;
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [cart, dispatchAction] = useReducer(cartReducer, undefined, loadCart);

  // Skipped on mount: the first value of `cart` is the one just read back from
  // storage, so writing it again is a whole-cart stringify and a synchronous
  // write on the load path, before the volunteer has touched anything.
  const restored = useRef(true);
  useEffect(() => {
    if (restored.current) {
      restored.current = false;
      return;
    }
    saveCart(cart);
  }, [cart]);

  // Stable for the life of the provider: it closes over nothing but
  // `dispatchAction`, which React guarantees. A `dispatch` that changed with
  // the cart would make every effect that depends on it re-run once per scan,
  // and the components that must not restart -- the camera, the deep link --
  // would each need a ref to hide it behind.
  const dispatch = useCallback((intent: CartIntent) => dispatchAction(toAction(intent)), []);

  // The cart it writes is the one the reducer is about to produce, from the
  // same action, so the device and the screen cannot disagree about which key
  // the next batch carries.
  const handOver = useCallback(() => {
    const action: CartAction = { type: "clear", idempotencyKey: mintIdempotencyKey() };
    saveCart(cartReducer(cart, action));
    dispatchAction(action);
  }, [cart]);

  const value = useMemo<CartContextValue>(
    () => ({ cart, dispatch, handOver }),
    [cart, dispatch, handOver],
  );

  return <CartContext value={value}>{children}</CartContext>;
}

export function useCart(): CartContextValue {
  const value = useContext(CartContext);
  if (value === null) {
    throw new Error("useCart must be called inside a <CartProvider>");
  }
  return value;
}
