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
}
