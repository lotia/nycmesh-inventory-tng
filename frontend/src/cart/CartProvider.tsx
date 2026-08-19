/**
 * The cart is shared by the scanner, the item list, the volunteer picker and
 * the submit bar, so it is one `useReducer` behind a context and no state
 * library. See docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import { createContext, type ReactNode, useContext, useEffect, useMemo, useReducer } from "react";
import { type CartAction, type CartState, cartReducer, mintIdempotencyKey } from "./cartState";
import { loadCart, saveCart } from "./cartStorage";

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/**
 * What a component dispatches: an action without the clock reading or the
 * fresh key, which the provider fills in so the reducer stays pure.
 */
export type CartIntent = DistributiveOmit<CartAction, "at" | "idempotencyKey" | "createdAt">;

export interface CartContextValue {
  cart: CartState;
  dispatch: (intent: CartIntent) => void;
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
        createdAt: new Date().toISOString(),
      };
    default:
      return intent;
  }
}

export function CartProvider({ children }: { children: ReactNode }) {
  const [cart, dispatchAction] = useReducer(cartReducer, undefined, loadCart);

  useEffect(() => {
    saveCart(cart);
  }, [cart]);

  const value = useMemo<CartContextValue>(
    () => ({ cart, dispatch: (intent) => dispatchAction(toAction(intent)) }),
    [cart],
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
