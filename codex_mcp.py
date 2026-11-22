import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from bridge import BRIDGE_HOST, BRIDGE_PORT, CALLBACK_TOKEN_FILE

server = FastMCP("astrbot-codex-bridge")
read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = build_opener(ProxyHandler({}), NoRedirects())

def request(route, body=None):
    data_dir = Path(os.environ["ASTRBOT_CODEX_BRIDGE_DATA_DIR"])
    token = (data_dir / CALLBACK_TOKEN_FILE).read_text(encoding="ascii").strip()
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(f"http://{BRIDGE_HOST}:{BRIDGE_PORT}{route}", data=data,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=95) as response:
            return json.loads(response.read(4 * 1024 * 1024))
    except HTTPError as exc:
        summary = "Request failed."
        try:
            details = json.loads(exc.read(8192))
            summary = details.get("error") or details.get("message") or summary
        except (ValueError, AttributeError):
            pass
        raise RuntimeError(f"HTTP {exc.code}: {summary}") from None

@server.tool(annotations=read)
def astrbot_status() -> dict:
    """Read live AstrBot/Codex bridge status."""
    return request("/status")

@server.tool()
def astrbot_notify(message: str, session: str = "") -> dict:
    """Forward a Codex execution result through AstrBot to the paired WeChat conversation when requested."""
    payload = {"message": message}
    if session:
        payload["session"] = session
    return request("/notify", payload)

if __name__ == "__main__":
    server.run(transport="stdio")
