"""``biblioflow-mcp``: run the MCP server over stdio."""

from __future__ import annotations

import sys

from biblioflow_mcp.client import ApiError, BiblioflowClient, Config
from biblioflow_mcp.server import build_server


def main() -> None:
    try:
        config = Config.from_env()
    except ApiError as exc:
        print(f"biblioflow-mcp: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    build_server(BiblioflowClient(config)).run(transport="stdio")


if __name__ == "__main__":
    main()
