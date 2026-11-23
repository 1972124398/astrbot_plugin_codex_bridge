"""Run beside Codex when AstrBot is in Docker or on another machine."""

import argparse
import hmac
from pathlib import Path
import secrets

from aiohttp import web

from desktop import DesktopClient


def create_app(desktop, token):
    @web.middleware
    async def authorize(request, handler):
        if not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(),
            ("Bearer " + token).encode(),
        ):
            raise web.HTTPUnauthorized()
        try:
            return await handler(request)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=503)

    app = web.Application(middlewares=[authorize], client_max_size=256 * 1024)

    async def tools(request):
        return web.json_response({"tools": await desktop.tools()})

    async def call(request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("tool"), str):
            raise ValueError("Expected tool and arguments JSON object.")
        return web.json_response(
            await desktop.call(data["tool"], data.get("arguments", {}))
        )

    app.add_routes([web.get("/tools", tools), web.post("/call", call)])
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=6197, type=int)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--pipe", default="")
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--controller-thread-id", default="")
    args = parser.parse_args()
    if not args.token_file.exists():
        args.token_file.parent.mkdir(parents=True, exist_ok=True)
        with args.token_file.open("x", encoding="ascii") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
        args.token_file.chmod(0o600)
    token = args.token_file.read_text(encoding="ascii").strip()
    if len(token) < 32:
        parser.error("Host token must contain at least 32 ASCII characters.")
    desktop = DesktopClient(
        {
            "desktop_pipe": args.pipe,
            "codex_home": args.codex_home,
            "controller_thread_id": args.controller_thread_id,
        }
    )
    print(f"Host token file: {args.token_file.resolve()}")
    web.run_app(
        create_app(desktop, token), host=args.host, port=args.port, access_log=None
    )


if __name__ == "__main__":
    main()
