"""compose.yaml's services, read as text."""

import re

from inventory_tng.core.local_env import CLONE

# A service: its name two spaces in, up to the next line as little indented.
_SERVICE = re.compile(r"^  ([\w-]+):\n(.*?)(?=^ {0,2}\S|\Z)", re.MULTILINE | re.DOTALL)


def services() -> dict[str, str]:
    """Each service's lines, by its name."""
    text = (CLONE / "compose.yaml").read_text()
    listed = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return dict(_SERVICE.findall(listed))
