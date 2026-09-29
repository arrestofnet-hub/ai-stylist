import argparse
import json
from pathlib import Path
from urllib.parse import urlparse


def normalize_mcp_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urlparse(value)

    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("MCP URL must be a public HTTPS URL")

    if not parsed.path or parsed.path == "/":
        value = value + "/mcp"
    elif not parsed.path.endswith("/mcp"):
        raise ValueError("MCP URL must end with /mcp")

    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate portable mcp.json for the deployed AI Stylist server."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Public MCP URL, for example https://stylist.example.com/mcp",
    )
    parser.add_argument(
        "--output",
        default="mcp.json",
        help="Output path (default: mcp.json)",
    )
    args = parser.parse_args()

    url = normalize_mcp_url(args.url)
    payload = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "ai_stylist": {
                "type": "streamable-http",
                "url": url,
            }
        },
    }

    output = Path(args.output)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output} for {url}")


if __name__ == "__main__":
    main()
