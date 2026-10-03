"""Tests ensuring all FastMCP tool schemas are compatible with Home Assistant's MCP client."""
import pytest
from src import server

try:
    from voluptuous_openapi import convert_to_voluptuous
    HAS_VOLUPTUOUS_OPENAPI = True
except ImportError:
    HAS_VOLUPTUOUS_OPENAPI = False


def test_no_defs_or_refs_in_tool_parameters():
    """Home Assistant's MCP client fails when tool parameters contain $defs or $ref.
    All schemas must be fully inlined.
    """
    tools = server.mcp._tool_manager.list_tools()
    assert len(tools) > 0, "Expected registered tools"

    for tool in tools:
        schema = tool.parameters
        assert "$defs" not in schema, f"Tool '{tool.name}' schema still contains $defs: {schema}"
        _assert_no_refs(schema, tool.name)


def _assert_no_refs(node, tool_name):
    if isinstance(node, dict):
        assert "$ref" not in node, f"Tool '{tool_name}' schema contains $ref: {node}"
        for v in node.values():
            _assert_no_refs(v, tool_name)
    elif isinstance(node, list):
        for item in node:
            _assert_no_refs(item, tool_name)


@pytest.mark.skipif(not HAS_VOLUPTUOUS_OPENAPI, reason="voluptuous-openapi not installed")
def test_voluptuous_openapi_compatibility():
    """Verify that every tool's schema can be parsed by voluptuous-openapi without error."""
    tools = server.mcp._tool_manager.list_tools()
    for tool in tools:
        # Must not raise ValueError: Invalid schema, missing type
        converted = convert_to_voluptuous(tool.parameters)
        assert converted is not None
