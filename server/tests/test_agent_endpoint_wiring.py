"""
Test the wiring of the agent endpoint in main.py.

Uses AST to verify:
1. POST /api/idp/agent endpoint is registered
2. AgentRequest has the required fields
3. HookDeps call passes all required field names
"""

import ast
from pathlib import Path
from typing import Any, Optional, Set


def get_main_py_ast() -> ast.Module:
    """Parse main.py and return the AST."""
    main_py = Path(__file__).parent.parent / "main.py"
    return ast.parse(main_py.read_text())


def find_post_route(tree: ast.Module, path: str) -> Optional[ast.AsyncFunctionDef]:
    """Find a @app.post() decorator route by path."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    if isinstance(decorator.func, ast.Attribute):
                        if (
                            isinstance(decorator.func.value, ast.Name)
                            and decorator.func.value.id == "app"
                            and decorator.func.attr == "post"
                        ):
                            # Get the path from decorator args
                            if decorator.args and isinstance(decorator.args[0], ast.Constant):
                                if decorator.args[0].value == path:
                                    return node
    return None


def find_class_def(tree: ast.Module, class_name: str) -> Optional[ast.ClassDef]:
    """Find a class definition by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    return None


def get_class_fields(class_def: ast.ClassDef) -> Set[str]:
    """Extract field names from a BaseModel class (from annotations)."""
    fields = set()
    for node in class_def.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.add(node.target.id)
    return fields


def find_function_def(tree: ast.Module, func_name: str) -> Optional[ast.AsyncFunctionDef]:
    """Find a function definition by name (async or sync)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return node
    return None


def extract_call_kwargs(call_node: ast.Call) -> Set[str]:
    """Extract all keyword argument names from a function call."""
    return {kw.arg for kw in call_node.keywords if kw.arg}


def find_hookdeps_call(func_def: ast.AsyncFunctionDef) -> Optional[ast.Call]:
    """Find the HookDeps(...) call inside a function."""
    for node in ast.walk(func_def):
        if isinstance(node, ast.Call):
            # Direct call: HookDeps(...)
            if isinstance(node.func, ast.Name) and node.func.id == "HookDeps":
                return node
            # Attribute call: _doc_agent_hooks.HookDeps(...)
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_doc_agent_hooks"
                    and node.func.attr == "HookDeps"
                ):
                    return node
    return None


def get_hookdeps_fields(hooks_module_path: str) -> Set[str]:
    """Extract HookDeps dataclass field names from doc_agent_hooks.py."""
    hooks_py = Path(hooks_module_path)
    tree = ast.parse(hooks_py.read_text())

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HookDeps":
            # Find the __init__ method or annotations
            fields = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.add(item.target.id)
            return fields
    return set()


def test_agent_endpoint_registered():
    """Test that POST /api/idp/agent is registered."""
    tree = get_main_py_ast()
    endpoint = find_post_route(tree, "/api/idp/agent")
    assert endpoint is not None, "POST /api/idp/agent endpoint not found"
    assert endpoint.name == "idp_agent", f"Unexpected function name: {endpoint.name}"


def test_agent_request_model():
    """Test that AgentRequest has the required fields."""
    tree = get_main_py_ast()
    req_class = find_class_def(tree, "AgentRequest")
    assert req_class is not None, "AgentRequest class not found"

    fields = get_class_fields(req_class)
    required_fields = {
        "conversation_id",
        "message",
        "attachments",
        "model_override",
        "user_message_id",
        "assistant_message_id",
    }
    assert required_fields.issubset(fields), (
        f"AgentRequest missing required fields. "
        f"Expected: {required_fields}, Found: {fields}"
    )


def test_agent_attachment_model():
    """Test that AgentAttachment has the required fields."""
    tree = get_main_py_ast()
    att_class = find_class_def(tree, "AgentAttachment")
    assert att_class is not None, "AgentAttachment class not found"

    fields = get_class_fields(att_class)
    required_fields = {"doc_id", "role"}
    assert required_fields.issubset(fields), (
        f"AgentAttachment missing required fields. "
        f"Expected: {required_fields}, Found: {fields}"
    )


def test_agent_endpoint_uses_agent_sse():
    """Test that the endpoint calls _doc_agent_service.agent_sse."""
    tree = get_main_py_ast()
    endpoint = find_post_route(tree, "/api/idp/agent")
    assert endpoint is not None

    # Look for calls to agent_sse
    found = False
    for node in ast.walk(endpoint):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_doc_agent_service"
                    and node.func.attr == "agent_sse"
                ):
                    found = True
                    break
    assert found, "Endpoint does not call _doc_agent_service.agent_sse"


def test_agent_endpoint_uses_build_hooks():
    """Test that the endpoint calls _doc_agent_hooks.build_hooks."""
    tree = get_main_py_ast()
    endpoint = find_post_route(tree, "/api/idp/agent")
    assert endpoint is not None

    # Look for calls to build_hooks
    found = False
    for node in ast.walk(endpoint):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "_doc_agent_hooks"
                    and node.func.attr == "build_hooks"
                ):
                    found = True
                    break
    assert found, "Endpoint does not call _doc_agent_hooks.build_hooks"


def test_hookdeps_wiring():
    """Test that all HookDeps fields are wired."""
    tree = get_main_py_ast()
    endpoint = find_post_route(tree, "/api/idp/agent")
    assert endpoint is not None

    # Find the HookDeps(...) call
    hookdeps_call = find_hookdeps_call(endpoint)
    assert hookdeps_call is not None, "HookDeps call not found in endpoint"

    # Extract the kwargs
    wired_fields = extract_call_kwargs(hookdeps_call)

    # Get the expected fields from doc_agent_hooks.py
    hooks_module = Path(__file__).parent.parent / "doc_agent_hooks.py"
    expected_fields = get_hookdeps_fields(str(hooks_module))

    assert expected_fields, "Could not parse HookDeps fields from doc_agent_hooks.py"

    missing = expected_fields - wired_fields
    assert not missing, (
        f"HookDeps wiring is missing fields: {missing}. "
        f"Wired: {wired_fields}, Expected: {expected_fields}"
    )


if __name__ == "__main__":
    test_agent_endpoint_registered()
    print("✓ Agent endpoint registered")

    test_agent_request_model()
    print("✓ AgentRequest model has required fields")

    test_agent_attachment_model()
    print("✓ AgentAttachment model has required fields")

    test_agent_endpoint_uses_agent_sse()
    print("✓ Endpoint calls agent_sse")

    test_agent_endpoint_uses_build_hooks()
    print("✓ Endpoint calls build_hooks")

    test_hookdeps_wiring()
    print("✓ HookDeps wiring is complete")

    print("\n✓ All wiring tests passed!")
