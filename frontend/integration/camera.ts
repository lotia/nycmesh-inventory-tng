/**
 * Asking Chromium for a camera it does not have.
 *
 * Two flags, needed by every spec that opens the camera and written here once
 * rather than in each of them: a synthetic capture device in place of a lens,
 * and a permission prompt answered before it is drawn. Without the second the
 * suite hangs on a dialog no test can click.
 */
export const FAKE_CAMERA = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"];
