/**
 * Tells a label shown again from the same label still in view: the camera
 * reads a code many times a second, so one is taken once in `quietMs`.
 */
export class Repeats {
  private readonly last = new Map<string, number>();

  constructor(private readonly quietMs = 2000) {}

  /** Whether `code`, read at `now` (in milliseconds), is a new scan. */
  fresh(code: string, now: number): boolean {
    const before = this.last.get(code);
    this.last.set(code, now);
    return before === undefined || now - before >= this.quietMs;
  }

  /** Reading stopped for `ms`: the time does not count toward any label's
   * quiet time, so a label held in view through the pause is not taken
   * again as reading resumes. */
  paused(ms: number) {
    for (const [code, at] of this.last) {
      this.last.set(code, at + ms);
    }
  }
}
