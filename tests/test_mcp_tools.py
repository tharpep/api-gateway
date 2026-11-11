import asyncio

from app.mcp_server import mcp

READ = (True, None, None, False)
READ_OPEN = (True, None, None, True)
WRITE = (False, False, False, False)
WRITE_IDEMPOTENT = (False, False, True, False)
WRITE_OPEN = (False, False, False, True)
DESTRUCTIVE = (False, True, False, False)
DESTRUCTIVE_IDEMPOTENT = (False, True, True, False)

GROUPS = {
    READ: """homelab_overview homelab_storage homelab_services homelab_container show_card
        calendar_today journal_summary kb_search tasks_list job_status bakeoff_status
        learning_summary list_jobs board_list board_card board_projects board_project plan_list
        plan_read plan_diff profile_list profile_read mcp_server_list vault_search vault_read
        vault_list vault_backlinks vault_unresolved vault_orphans vault_tags""",
    READ_OPEN: "multi_search",
    WRITE: """journal_add tasks_add delegate_code_task continue_job resume_job start_bakeoff
        promote_bakeoff board_file_card board_add_project board_add_workstream plan_save
        plan_create profile_save mcp_server_save vault_create vault_append vault_file_note""",
    WRITE_IDEMPOTENT: """homelab_start_container board_move_card board_update_card
        board_update_cards vault_set_property""",
    WRITE_OPEN: "send_to_phone research second_opinion kb_ingest_url",
    DESTRUCTIVE: "homelab_restart_container vault_edit",
    DESTRUCTIVE_IDEMPOTENT: """cancel_job vault_delete board_update_project
        board_update_workstream plan_update""",
}
EXPECTED = {name: hints for hints, names in GROUPS.items() for name in names.split()}


def _registered() -> dict[str, tuple]:
    tools = asyncio.run(mcp.list_tools())
    return {
        t.name: (
            t.annotations.readOnlyHint,
            t.annotations.destructiveHint,
            t.annotations.idempotentHint,
            t.annotations.openWorldHint,
        )
        for t in tools
    }


def test_tool_names_and_annotations_are_pinned():
    """Adding, renaming or re-annotating a tool must change this file in the same commit."""
    assert _registered() == EXPECTED
