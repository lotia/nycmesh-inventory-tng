/**
 * Telling the volunteer the scan landed without making them look.
 *
 * They are holding a phone at a shelf with the other hand in a box, so the
 * alert on screen is the slowest of the three ways this app can answer. A
 * short tone and a tap on the wrist are the fast ones, and neither replaces
 * the alert: this is confirmation, not the message.
 *
 * Both are best-effort by design. A browser with no vibration motor, a tab
 * that has not been interacted with yet, a device on silent -- none of those
 * is a failure worth telling anybody about, because the cart on screen is
 * already the truth.
 */

/** High enough to cut through a basement, short enough not to be a nuisance. */
const TONE_HZ = 1760;
const TONE_MS = 90;
const VIBRATE_MS = 40;
/** Well below full scale: this plays next to somebody's ear, in a quiet room. */
const LEVEL = 0.15;

/**
 * The one audio context this app ever opens.
 *
 * An AudioContext is a hardware resource and a phone allows only a handful, so
 * a scan per second must not open one per scan. Nor can each be closed after
 * its tone: a context created before the tab has had a qualifying gesture
 * starts suspended, its clock never advances, the oscillator never ends, and
 * the close that was hung off `onended` never runs -- which is exactly how the
 * handful runs out and the tone goes silent for the rest of the session.
 *
 * Created on the first scan rather than at import, because constructing one
 * eagerly is what browsers autoplay policies object to, and the
 * list-and-stepper volunteer may never scan anything at all.
 */
let shared: AudioContext | null = null;

function audio(): AudioContext {
  shared ??= new AudioContext();
  // Suspended is the ordinary state until the tab has been interacted with.
  // Scanning is an interaction, so this is the moment it can be resumed --
  // and resuming an already-running context is a no-op.
  void shared.resume?.();
  return shared;
}

/** Lets a test start again from nothing. Not used by the app. */
export function forgetAudio(): void {
  void shared?.close();
  shared = null;
}

export function confirmScan(): void {
  // Haptic first, and separately: it is the one that works with the phone in a
  // pocket or the room too loud, and it must not be lost to an audio failure.
  navigator.vibrate?.(VIBRATE_MS);
  try {
    const context = audio();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const until = context.currentTime + TONE_MS / 1000;

    oscillator.frequency.value = TONE_HZ;
    // Cut to silence and the speaker clicks, which sounds like a fault. Ramp
    // it -- exponentially, so it has to end above zero.
    gain.gain.setValueAtTime(LEVEL, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(LEVEL / 100, until);
    oscillator.connect(gain).connect(context.destination);

    oscillator.start();
    oscillator.stop(until);
    // Nothing to close: the context is shared and the nodes are disconnected
    // when the oscillator ends, which the browser does for a stopped source.
  } catch {
    // No Web Audio, or the tab is not allowed to make a sound yet. The alert
    // on screen says the same thing.
  }
}
