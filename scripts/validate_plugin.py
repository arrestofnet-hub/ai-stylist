import ast
import json
from pathlib import Path

REQUIRED_HINTS = {"read_only_hint", "open_world_hint", "destructive_hint"}

root = Path(__file__).resolve().parents[1]
plugin = json.loads((root / "plugin.json").read_text(encoding="utf-8"))

assert plugin["version"] == "0.6.0", "plugin.json version must be 0.6.0"
interface = plugin["extensions"]["com.openai"]["interface"]
assert len(interface["shortDescription"]) <= 30, "shortDescription must be <= 30 characters"
assert interface["displayName"].strip(), "displayName is required"
assert len(interface["defaultPrompt"]) >= 4, "starter prompts are required"

tree = ast.parse((root / "mcp_server.py").read_text(encoding="utf-8"))
tools = []

for node in tree.body:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        fn = decorator.func
        if not (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "server"
            and fn.attr == "tool"
        ):
            continue
        annotations = next(
            (kw.value for kw in decorator.keywords if kw.arg == "annotations"),
            None,
        )
        if not isinstance(annotations, ast.Call):
            raise AssertionError(f"{node.name}: annotations=ToolAnnotations(...) is required")
        keys = {kw.arg for kw in annotations.keywords if kw.arg}
        missing = REQUIRED_HINTS - keys
        if missing:
            raise AssertionError(
                f"{node.name}: missing explicit annotations: {sorted(missing)}"
            )
        tools.append(node.name)

assert tools, "No MCP tools found"
assert len(tools) >= 15, f"Unexpected MCP tool count: {len(tools)}"

submission = (root / "SUBMISSION.md").read_text(encoding="utf-8")
assert submission.count("### P") == 5, "SUBMISSION.md must contain exactly 5 positive tests"
assert submission.count("### N") == 3, "SUBMISSION.md must contain exactly 3 negative tests"

print(f"plugin validation ok: {len(tools)} tools, 5 positive tests, 3 negative tests")
