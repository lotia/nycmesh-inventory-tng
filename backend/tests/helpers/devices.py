"""Registered devices."""

from datetime import timedelta

from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.models import Device

ADDRESS = "192.0.2.10"


def new_device(session: Session) -> tuple[Device, str]:
    """A registered device and its cookie, for tests of what needs one."""
    _, phrase = devices.create_code(session, max_uses=1, lifetime=timedelta(days=1))
    return devices.register(session, phrase, ADDRESS)
