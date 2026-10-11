import pytest

from nexus_mcp.core.errors import ConfirmationRequired, WriteNotPermitted
from nexus_mcp.services.write_guard import WritePolicy


def policy(**kw):  # type: ignore[no-untyped-def]
    base = {
        "allow_writes": True,
        "allow_deletes": True,
        "writable": frozenset({"notes"}),
        "max_rows": 5,
    }
    return WritePolicy(**{**base, **kw})


def test_writes_disabled() -> None:
    with pytest.raises(WriteNotPermitted, match="disabled"):
        policy(allow_writes=False).check("insert", "notes")


def test_deletes_disabled_but_updates_ok() -> None:
    p = policy(allow_deletes=False)
    p.check("update", "notes")
    with pytest.raises(WriteNotPermitted, match="Delete"):
        p.check("delete", "notes")


def test_table_must_be_writable() -> None:
    with pytest.raises(WriteNotPermitted, match="not writable"):
        policy().check("insert", "other")
    with pytest.raises(WriteNotPermitted):
        policy(writable=frozenset()).check("insert", "notes")


def test_row_budget() -> None:
    policy().check_row_budget(5)
    with pytest.raises(WriteNotPermitted, match="6 rows"):
        policy().check_row_budget(6)


def test_confirmation() -> None:
    WritePolicy.check_confirmation(3, 3)
    with pytest.raises(ConfirmationRequired):
        WritePolicy.check_confirmation(None, 3)
    with pytest.raises(ConfirmationRequired, match="Nothing was changed"):
        WritePolicy.check_confirmation(2, 3)


def test_version_check_and_notion_policy_labels() -> None:
    WritePolicy.check_version("v1", "v1")
    with pytest.raises(ConfirmationRequired):
        WritePolicy.check_version(None, "v1")
    with pytest.raises(ConfirmationRequired):
        WritePolicy.check_version("v0", "v1")
    p = policy(scope_noun="parent", scope_env="NOTION_WRITABLE_PARENTS")
    with pytest.raises(WriteNotPermitted) as e:
        p.check("update", ["x", "y"])
    assert "NOTION_WRITABLE_PARENTS" in e.value.to_client_message()
    p.check("update", ["x", "notes"])  # any candidate in the allowlist authorises
