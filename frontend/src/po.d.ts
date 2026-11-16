/** A catalogue, as Lingui's Vite plugin compiles it when imported. */
declare module "*.po" {
  import type { Messages } from "@lingui/core";
  export const messages: Messages;
}
