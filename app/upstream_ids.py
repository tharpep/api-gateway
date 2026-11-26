"""Validators for tool arguments that become part of an upstream URL path or query.

A value that reaches a path unchecked can carry `../`, `?` or `#` and send the request to a
different route on the same upstream (api-gateway#59 review: a crafted card id approved a job).
Each validator accepts only the identifier's real shape and returns a canonical form, so what
reaches the URL is always one plain segment. Pinned by tests/test_upstream_ids.py.
"""

import re
import uuid

from mcp.server.fastmcp.exceptions import ToolError

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}", re.ASCII)
_REGISTRY_NAME = re.compile(r"[a-z][a-z0-9_-]{1,30}", re.ASCII)
_BAKEOFF_ID = re.compile(r"[0-9a-f]{8}", re.ASCII)
_UUID_TEXT = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"
)


def _refuse(what: str, value: object, shape: str) -> ToolError:
    return ToolError(f"{what} must be {shape}, got {value!r}")


def uuid_id(value: str, what: str) -> str:
    """A uuid in canonical lowercase hyphenated form."""
    if not isinstance(value, str) or not _UUID_TEXT.fullmatch(value):
        raise _refuse(what, value, "a uuid")
    return str(uuid.UUID(value))


def item_id(card_id: str) -> str:
    """The bare uuid of a backlog card given as item:<uuid> or <uuid>."""
    kind, _, raw = card_id.rpartition(":")
    if kind not in ("", "item"):
        raise ToolError("Only backlog cards (item:<uuid>) can be changed; job cards can't")
    return uuid_id(raw, "card_id")


def card_id(value: str) -> str:
    """A board card id, item:<uuid> or job:<uuid>, canonicalised."""
    kind, _, raw = value.partition(":")
    if kind not in ("item", "job"):
        raise _refuse("card_id", value, "item:<uuid> or job:<uuid>")
    return f"{kind}:{uuid_id(raw, 'card_id')}"


def slug(value: str, what: str = "slug") -> str:
    """A project or plan slug: lowercase a-z 0-9 and hyphens, starting with a letter or digit."""
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise _refuse(what, value, "a slug (a-z 0-9 -, starting with a letter or digit)")
    return value


def registry_name(value: str, what: str = "name") -> str:
    """An agent profile or MCP server name, as Sazed's /agents routes accept it."""
    if not isinstance(value, str) or not _REGISTRY_NAME.fullmatch(value):
        raise _refuse(what, value, "2 to 31 of a-z 0-9 _ - starting with a letter")
    return value


def bakeoff_id(value: str) -> str:
    """A bake-off id: 8 lowercase hex characters."""
    if not isinstance(value, str) or not _BAKEOFF_ID.fullmatch(value):
        raise _refuse("bakeoff_id", value, "8 hex characters")
    return value
