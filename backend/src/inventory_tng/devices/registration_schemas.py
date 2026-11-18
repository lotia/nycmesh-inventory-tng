"""The JSON request and reply schemas of registering a device
(devices/registration_api.py)."""

from pydantic import BaseModel

from inventory_tng.devices.devices import BadCode


class Registration(BaseModel):
    # The phrase as typed, or as the QR code's link carries it.
    code: str


class Registered(BaseModel):
    device_id: int


class CodeRefusedReply(BaseModel):
    reason: BadCode
