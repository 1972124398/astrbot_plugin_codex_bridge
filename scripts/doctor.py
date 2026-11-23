"""Read-only diagnostics. Never submits a task or sends a message."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import DesktopClient  # noqa: E402


async def diagnose(settings):
    client = DesktopClient(settings)
    tools = await client.tools()
    listing = await client.all_threads()
    local = [
        row
        for row in listing["threads"]
        if row.get("kind", "codex") == "codex" and row.get("hostId", "local") == "local"
    ]
    read_ok = False
    if local:
        detail = await client.call(
            "read_thread",
            {
                "threadId": local[0]["id"],
                "turnLimit": 1,
                "includeTurns": False,
                "maxOutputCharsPerItem": 0,
            },
        )
        read_ok = "thread" in detail
    return {
        "ok": True,
        "tool_count": len(tools),
        "thread_count": len(listing["threads"]),
        "local_thread_read": read_ok,
        "unavailable_hosts": listing.get("unavailableHosts", []),
        "instant_interrupt_available": any(
            t["name"] in ("interrupt_thread", "stop_thread") for t in tools
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipe", default="")
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--controller-thread-id", default="")
    args = parser.parse_args()
    try:
        result = asyncio.run(
            diagnose(
                {
                    "desktop_pipe": args.pipe,
                    "codex_home": args.codex_home,
                    "controller_thread_id": args.controller_thread_id,
                }
            )
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
