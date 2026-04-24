#!/usr/bin/env python3
# This is AI generated code
"""Send a single WebSocket command to a running HA instance.

Used by the docker test harness to query things HA only
exposes over its WebSocket API (e.g. ``blueprint/list``).
Runs inside the HA container so it can use ``aiohttp``,
which the HA image already ships -- no extra deps needed
on either the container side or the test-runner side.

Usage::

    ws_query.py <access_token> '<json command>'

Prints the response payload as JSON on stdout. Exits
non-zero on transport errors or non-success WS responses.
"""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp

WS_URL = "ws://localhost:8123/api/websocket"


async def _run(token: str, command: dict[str, object]) -> dict[str, object]:
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(
            WS_URL, timeout=aiohttp.ClientTimeout(total=30)
        ) as ws,
    ):
        # HA's auth dance: server says auth_required,
        # client sends auth + token, server says auth_ok.
        first = await ws.receive_json()
        if first.get("type") != "auth_required":
            msg = f"unexpected first message: {first!r}"
            raise RuntimeError(msg)
        await ws.send_json({"type": "auth", "access_token": token})
        ack = await ws.receive_json()
        if ack.get("type") != "auth_ok":
            msg = f"auth failed: {ack!r}"
            raise RuntimeError(msg)

        # Issue the command. WS commands need a numeric id;
        # ours is single-shot so 1 is fine.
        payload = dict(command)
        payload["id"] = 1
        await ws.send_json(payload)
        reply: dict[str, object] = await ws.receive_json()
        return reply


def main() -> int:
    if len(sys.argv) != 3:
        sys.stderr.write(
            "usage: ws_query.py <access_token> '<json command>'\n",
        )
        return 2
    token = sys.argv[1]
    try:
        command = json.loads(sys.argv[2])
    except json.JSONDecodeError as e:
        sys.stderr.write(f"invalid command JSON: {e}\n")
        return 2
    if not isinstance(command, dict):
        sys.stderr.write("command must be a JSON object\n")
        return 2

    reply = asyncio.run(_run(token, command))
    print(json.dumps(reply))
    if not reply.get("success", True):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
