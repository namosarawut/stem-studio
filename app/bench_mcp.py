"""A tiny MCP server (stdio, JSON-RPC 2.0) that gives Claude Code the metronome tools.

Started by `claude -p` through --mcp-config: python -m app.bench_mcp <state.json>. The
state file holds the working copy of the grid (see assistant.Workbench.to_state); every
change is written back so Prism can read the result when the CLI exits.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .assistant import TOOLS, Workbench

PROTOCOL = "2025-06-18"


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main(state_path: Path) -> None:
    bench = Workbench.from_state(json.loads(state_path.read_text(encoding="utf-8")))
    handlers = {
        "overview": lambda _: bench.overview(),
        "get_beats": lambda a: bench.get_beats(float(a["start"]), float(a["end"])),
        "apply_auto_repair": lambda _: bench.apply_auto_repair(),
        "replace_beats": lambda a: bench.replace_beats(float(a["start"]), float(a["end"]), a["beats"], a["bar_lines"]),
        "check_lock": lambda _: bench.check_lock(),
    }
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, ident = request.get("method"), request.get("id")
        if ident is None:  # a notification (e.g. notifications/initialized): no reply
            continue
        if method == "initialize":
            version = (request.get("params") or {}).get("protocolVersion") or PROTOCOL
            result = {"protocolVersion": version, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "prism", "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]} for t in TOOLS]}
        elif method == "tools/call":
            params = request.get("params") or {}
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                output = handlers[name](args)
                state_path.write_text(json.dumps(bench.to_state()), encoding="utf-8")
                result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}]}
            except Exception as error:  # report to Claude, keep serving
                result = {"content": [{"type": "text", "text": f"error: {error}"}], "isError": True}
        else:
            _send({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"unknown method {method}"}})
            continue
        _send({"jsonrpc": "2.0", "id": ident, "result": result})


if __name__ == "__main__":
    main(Path(sys.argv[1]))
