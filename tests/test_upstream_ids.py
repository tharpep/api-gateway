import pytest
from mcp.server.fastmcp.exceptions import ToolError

from app import upstream_ids as ids

UUID = "4b1e0c2a-33ac-4055-aa8c-8c1f7b5541a7"

HOSTILE = [
    "",
    ".",
    "..",
    "a/b",
    "a\\b",
    "a?b",
    "a#b",
    "a%2fb",
    "%2e%2e",
    "a\nb",
    "a\x00",
    "a b",
    f"{UUID}/",
    f"{UUID}?",
    f"{UUID}\n",
    f"../{UUID}",
    "http://evil.test",
    "é",
]


@pytest.mark.parametrize("value", HOSTILE)
@pytest.mark.parametrize(
    "check",
    [
        lambda v: ids.uuid_id(v, "id"),
        ids.item_id,
        ids.card_id,
        ids.slug,
        ids.registry_name,
        ids.bakeoff_id,
    ],
)
def test_hostile_values_are_refused(check, value):
    with pytest.raises(ToolError):
        check(value)


def test_a_uuid_comes_back_canonical():
    assert ids.uuid_id(UUID.upper().replace("-", ""), "id") == UUID


def test_uuid_refuses_the_urn_and_brace_forms_uuid_accepts():
    for value in (f"urn:uuid:{UUID}", f"{{{UUID}}}"):
        with pytest.raises(ToolError):
            ids.uuid_id(value, "id")


def test_item_id_takes_a_bare_or_prefixed_uuid_and_refuses_job_cards():
    assert ids.item_id(f"item:{UUID}") == ids.item_id(UUID) == UUID
    with pytest.raises(ToolError, match="job cards"):
        ids.item_id(f"job:{UUID}")


def test_card_id_keeps_its_kind():
    assert ids.card_id(f"job:{UUID.upper()}") == f"job:{UUID}"
    with pytest.raises(ToolError):
        ids.card_id(f"dispatch:{UUID}")


@pytest.mark.parametrize(
    ("check", "good"),
    [
        (ids.slug, "board-visibility"),
        (ids.registry_name, "change_opus-2"),
        (ids.bakeoff_id, "0a1b2c3d"),
    ],
)
def test_real_shapes_pass_unchanged(check, good):
    assert check(good) == good
