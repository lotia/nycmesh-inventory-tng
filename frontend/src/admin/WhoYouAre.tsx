/**
 * The way in, and the way out, for the one population that has one.
 *
 * Decision 0012 settles that volunteers do not sign in and administrators do,
 * and decision 0014 puts what an administrator does inside this app rather
 * than in a second one. Between them they imply a control this app did not
 * have: a volunteer needs nothing here, and an administrator needs somewhere
 * to start.
 *
 * SMALL ON PURPOSE, AND THAT IS THE DESIGN RATHER THAN RESTRAINT. This screen
 * is a volunteer's, held in one hand, in a basement, next to a scanner. A
 * prominent Sign in would be an invitation to the population decision 0012
 * says must never need one -- so this is a text button in the corner, and
 * anybody who does not need it can go a year without noticing it.
 *
 * WHY THE THIRD STATE IS DRAWN AT ALL. Signing in does not make somebody an
 * administrator: decision 0013 point 5 keeps identity and authority apart on
 * purpose, so a new account is ordinary until an existing administrator says
 * otherwise. That leaves a person who has done everything right, is signed in,
 * and sees an app with no administrative controls in it -- identical to what
 * they saw before signing in, and identical again to a session gone stale.
 * Three different problems wearing one face, and the likeliest reading of all
 * three is that the application is broken. Saying which one it is costs a
 * sentence.
 */
import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { accountsPage } from "./accounts";
import { useCurrentSession, useSessionSettled } from "./SessionProvider";

export function WhoYouAre() {
  const me = useCurrentSession();
  const settled = useSessionSettled();

  // NOTHING UNTIL THE SERVER HAS ANSWERED, which is the one thing this
  // component must get right and the reason `useSessionSettled` exists.
  // `/api/me` says nobody is signed in until it says otherwise, so drawing
  // from the unsettled answer would show every administrator a Sign in button
  // on every load and then swap it for their own name -- a control that was
  // wrong, briefly, every single time. An empty corner for one read is
  // cheaper, and nothing else on this screen is waiting on it.
  if (!settled) {
    return null;
  }

  if (!me.authenticated) {
    return (
      <Button size="small" href={accountsPage("login/")}>
        Sign in
      </Button>
    );
  }

  return (
    <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
      <Typography variant="body2" color="text.secondary">
        {me.username}
      </Typography>
      <Button size="small" href={accountsPage("logout/")}>
        Sign out
      </Button>
    </Stack>
  );
}

/**
 * The sentence for somebody signed in who is not an administrator.
 *
 * Separate from the control above because it belongs in a different place on
 * the screen: the control is a corner, and this is a thing to read. It is the
 * only state here that needs explaining, so it is the only one that gets a
 * line -- an administrator already knows, and a volunteer never sees it.
 */
export function NotAnAdministrator() {
  const me = useCurrentSession();
  const settled = useSessionSettled();

  // `!settled` is implied by `!me.authenticated` today, because ANONYMOUS
  // carries `authenticated: false` while the read is in flight. It is kept
  // anyway, and not for symmetry: a check that is correct only because of a
  // neighbouring value's default is the shape this whole change exists to
  // remove, and it is exactly how StaleSession came to be safe by accident.
  // Asking the question this component actually means costs nothing.
  if (!settled || !me.authenticated || me.administrator) {
    return null;
  }
  return (
    <Typography variant="body2" color="text.secondary">
      You are signed in as {me.username}, and this account is not an administrator. Administrative
      access is granted by somebody who already has it, rather than by signing in.
    </Typography>
  );
}
