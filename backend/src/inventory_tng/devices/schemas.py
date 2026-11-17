"""The devices' JSON request and reply schemas: registering, and the
replies to a refused device or code, which other endpoints declare too."""

from typing import Literal

from pydantic import BaseModel

from inventory_tng.devices.devices import BadCode, Refusal


class Registration(BaseModel):
    # The phrase as typed, or as the QR code's link carries it.
    code: str


class Registered(BaseModel):
    device_id: int


class CodeRefusedReply(BaseModel):
    reason: BadCode


class RateLimitedReply(BaseModel):
    reason: Literal["rate_limited"]


class DeviceRefusedReply(BaseModel):
    reason: Refusal
