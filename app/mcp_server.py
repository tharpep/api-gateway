"""MCP endpoint (streamable HTTP) for Claude Code, Claude Desktop and agy.

Mounted at /mcp/ by app.main. Tools call the same router functions the REST API uses, so the
MCP surface can never drift from it or bypass its limits (e.g. Proxmox ACLs on restarts).
Reached over the tailnet at https://sazed.tail77c590.ts.net/gw/mcp/, where Caddy injects the
gateway key; direct LAN callers must send the key themselves.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from app.dependencies import verify_api_key
from app.routers import homelab, services

mcp = FastMCP(
    "sazed-gateway",
    instructions=(
        "Tools for Pryce's homelab and personal services, served by his api-gateway. "
        "Homelab tools read Proxmox status and can start or restart containers; Proxmox "
        "itself decides which containers are allowed (CT 108 never is). Restarting briefly "
        "takes that service down, so confirm with the user before restarting."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # The SDK's default DNS-rebinding check only accepts localhost Host headers, which would
    # reject traffic arriving via Caddy/Tailscale. Auth is the API key, and Caddy blocks
    # cross-site browser requests, so the check adds nothing here.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


async def _call(coro):
    try:
        return await coro
    except HTTPException as e:
        raise ToolError(str(e.detail)) from e


@mcp.tool()
async def homelab_overview() -> dict:
    """Proxmox host usage (CPU, memory, disk, storage) plus every container's status and usage."""
    return {
        "host": await _call(homelab.host_status()),
        "containers": await _call(homelab.list_containers()),
    }


@mcp.tool()
async def homelab_services(group: str | None = None) -> dict:
    """Health of every registered service (Sazed system, apps, infra): up/down, detail,
    since when, and maintenance/mute state. Optional group: sazed | apps | infra."""
    result = await _call(services.list_services(group, compact=True))
    return {
        "summary": result["summary"],
        "last_cycle_at": result["last_cycle_at"],
        "services": result["services"],
    }


@mcp.tool()
async def homelab_container(vmid: int) -> dict:
    """One container's live status, key config, and recent start/stop/reboot history."""
    return await _call(homelab.get_container(vmid, task_limit=10))


@mcp.tool()
async def homelab_restart_container(vmid: int) -> dict:
    """Reboot a running container. Its services go down briefly. Confirm with the user first.
    Only containers the gateway's Proxmox power token is allowed to manage can be restarted."""
    return await _call(homelab.restart_container(vmid))


@mcp.tool()
async def homelab_start_container(vmid: int) -> dict:
    """Start a stopped container. Only containers the gateway's power token may manage."""
    return await _call(homelab.start_container(vmid))


class _RequireApiKey:
    """ASGI guard: FastAPI route dependencies don't apply to mounted apps, so check the key
    here with the same verify_api_key the REST routers use."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            try:
                verify_api_key(Request(scope))
            except HTTPException as e:
                await JSONResponse({"detail": e.detail}, status_code=e.status_code)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def build_app():
    """The ASGI app to mount. Must be called before the lifespan starts the session manager."""
    return _RequireApiKey(mcp.streamable_http_app())
