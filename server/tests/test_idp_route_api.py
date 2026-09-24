"""
Tests for IDP routing logic extracted as pure functions in server/doc_graph.py.

Tests pick_routed_model and plan_route_request without importing main.py
or triggering side effects.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import doc_graph as _doc_graph


class TestPickRoutedModel:
    """Tests for doc_graph.pick_routed_model pure function."""

    def test_override_takes_precedence(self):
        """Override takes precedence over routed model."""
        meta = {
            "route_override": "special_model:latest",
            "route": {
                "category": "docs",
                "model": "normal_model:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "docs")
        assert result == "special_model:latest"

    def test_override_takes_precedence_for_ocr(self):
        """Override takes precedence even for non-text categories."""
        meta = {
            "route_override": "my_ocr:latest",
            "route": {
                "category": "text",
                "model": "text_model:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "ocr")
        assert result == "my_ocr:latest"

    def test_routed_model_for_text_category(self):
        """Routed model is used for text operations."""
        meta = {
            "route": {
                "category": "text",
                "model": "routed_text:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "text")
        assert result == "routed_text:latest"

    def test_routed_model_for_docs_category(self):
        """Routed model is used for docs operations."""
        meta = {
            "route": {
                "category": "docs",
                "model": "routed_docs:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "docs")
        assert result == "routed_docs:latest"

    def test_routing_ignored_for_ocr_operations(self):
        """Routed model is ignored for OCR operations."""
        meta = {
            "route": {
                "category": "docs",
                "model": "routed_model:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "ocr")
        assert result is None

    def test_routing_ignored_for_tables_operations(self):
        """Routed model is ignored for table operations."""
        meta = {
            "route": {
                "category": "text",
                "model": "routed_model:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "tables")
        assert result is None

    def test_no_routing_when_cache_absent(self):
        """Returns None when route not cached and no override."""
        meta = {}
        result = _doc_graph.pick_routed_model(meta, "docs")
        assert result is None

    def test_empty_model_in_route_ignored(self):
        """Empty model in routed result is ignored."""
        meta = {
            "route": {
                "category": "docs",
                "model": "",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "docs")
        assert result is None

    def test_routed_category_ocr_ignored(self):
        """Routed category 'ocr' is ignored for text ops."""
        meta = {
            "route": {
                "category": "ocr",
                "model": "ocr_model:latest",
            },
        }
        result = _doc_graph.pick_routed_model(meta, "text")
        assert result is None


class TestPlanRouteRequest:
    """Tests for doc_graph.plan_route_request pure function."""

    def test_override_present_with_value(self):
        """Present override with non-empty string: store, force=True, pass."""
        meta = {}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=True, override_value="new_model:latest", force=False
        )
        assert force is True
        assert override_to_pass == "new_model:latest"
        assert meta["route_override"] == "new_model:latest"

    def test_override_present_with_empty_string(self):
        """Present override with empty string: clear, force=True, pass None."""
        meta = {"route_override": "old_model:latest"}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=True, override_value="", force=False
        )
        assert force is True
        assert override_to_pass is None
        assert "route_override" not in meta

    def test_override_present_with_none(self):
        """Present override with None: clear, force=True, pass None."""
        meta = {"route_override": "old_model:latest"}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=True, override_value=None, force=False
        )
        assert force is True
        assert override_to_pass is None
        assert "route_override" not in meta

    def test_override_absent_no_stored(self):
        """Override absent, no stored: return force=False, None."""
        meta = {}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=False, override_value=None, force=False
        )
        assert force is False
        assert override_to_pass is None

    def test_override_absent_with_stored(self):
        """Override absent but stored: reuse stored, return force unchanged."""
        meta = {"route_override": "stored_model:latest"}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=False, override_value=None, force=False
        )
        assert force is False  # Unchanged
        assert override_to_pass == "stored_model:latest"

    def test_override_absent_with_stored_force_true(self):
        """Override absent, stored, force=True: reuse stored with force."""
        meta = {"route_override": "stored_model:latest"}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=False, override_value=None, force=True
        )
        assert force is True  # Unchanged
        assert override_to_pass == "stored_model:latest"

    def test_null_clears_and_forces_regression(self):
        """
        Regression test: null override clears stored AND forces recomputation.
        Previously null cleared override but did not force recompute.
        """
        meta = {"route_override": "old_model:latest", "route": {"cached": True}}
        force, override_to_pass = _doc_graph.plan_route_request(
            meta, override_present=True, override_value=None, force=False
        )
        # Should force recomputation and clear override
        assert force is True, "Null override must force recomputation"
        assert override_to_pass is None
        assert "route_override" not in meta


class TestMainPyRoutes:
    """
    Test that main.py has the required endpoints registered via @app decorators.
    Uses regex to avoid importing main.py.
    """

    def test_required_routes_exist(self):
        """Verify main.py defines the three IDP route endpoints."""
        main_path = Path(__file__).parent.parent / "main.py"
        with open(main_path) as f:
            content = f.read()

        # Search for the route decorators using simple regex
        required_routes = [
            "/api/idp/ingest-text",
            "/api/idp/route",
            "/api/idp/route/status",
        ]

        for route in required_routes:
            # Look for @app.post("/api/idp/route") or @app.get(...) patterns
            # Escape the forward slashes for regex
            escaped_route = route.replace("/", r"\/")
            patterns = [
                rf'@app\.post\(["\']/{escaped_route.lstrip("/")}"',
                rf'@app\.get\(["\']/{escaped_route.lstrip("/")}"',
                rf'@app\.post\(r["\']/{escaped_route.lstrip("/")}"',
                rf'@app\.get\(r["\']/{escaped_route.lstrip("/")}"',
                f'"{route}"',
                f"'{route}'",
            ]
            found = any(re.search(pat, content) for pat in patterns)
            assert found, f"Route {route} not found in main.py"
