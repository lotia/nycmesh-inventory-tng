"""The devices' pages' paths, which other features' pages link to."""

from inventory_tng.web.paths import ADMIN

DEVICES = f"{ADMIN}/devices"
CODES = f"{DEVICES}/codes"
# A device's own page (inventory-tng-8i4.60.13): what is known of it, its
# name, and revoking it alone, after a confirmation. `:int`, so the list's
# own paths under DEVICES are never taken for a device's.
DEVICE = f"{DEVICES}/{{device_id:int}}"
DEVICE_LIST = f"{DEVICES}/all"
# Not under DEVICE_LIST: {DEVICES}/{device_id}/revoke would take it.
BULK_REVOKE = f"{DEVICES}/revoke"

# The page a registration code's link and QR code open (bundle.PAGES).
REGISTER_PATH = "/register"
