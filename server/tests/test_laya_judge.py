"""
Tests for Laya judge_chat_category function.

Tests the chat auto-router Laya classification with confidence gating.
Does NOT import main.py to avoid initialization side-effects.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import pytest
from unittest.mock import Mock, patch
import laya_decider as _laya


class TestJudgeChatCategory:
    """Test laya_decider.judge_chat_category with mocked Laya router."""

    def test_returns_category_when_confident(self):
        """Returns category when confidence >= min_confidence."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = {
                "choice": "code",
                "confidence": 0.85,
                "probabilities": {},
            }

            criteria = {"trivial": "", "code": "", "tools": "", "default": ""}
            result = _laya.judge_chat_category("debug this script", criteria)

            assert result == "code"

    def test_returns_none_when_below_min_confidence(self):
        """Returns None when confidence < min_confidence."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = {
                "choice": "tools",
                "confidence": 0.65,
                "probabilities": {},
            }

            criteria = {"tools": ""}
            result = _laya.judge_chat_category(
                "what's the weather",
                criteria,
                min_confidence=0.75
            )

            assert result is None

    def test_short_category_needs_higher_confidence(self):
        """'short' category requires >= 0.9 confidence due to bias."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            # Below LAYA_SHORT_MIN_CONFIDENCE (0.9)
            mock_choice.return_value = {
                "choice": "short",
                "confidence": 0.85,
                "probabilities": {},
            }

            criteria = {"short": "simple factual"}
            result = _laya.judge_chat_category("what is 2+2?", criteria)

            assert result is None

            # Exactly at threshold
            mock_choice.return_value["confidence"] = 0.9
            result = _laya.judge_chat_category("what is 2+2?", criteria)
            assert result == "short"

    def test_returns_none_when_invalid_category(self):
        """Returns None when returned category not in criteria."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = {
                "choice": "vision",  # Not in criteria
                "confidence": 0.95,
                "probabilities": {},
            }

            criteria = {"trivial": "", "code": "", "default": ""}
            result = _laya.judge_chat_category("show me this", criteria)

            assert result is None

    def test_returns_none_when_judge_choice_returns_none(self):
        """Returns None when judge_choice unavailable."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = None

            criteria = {"code": ""}
            result = _laya.judge_chat_category("debug", criteria)

            assert result is None

    def test_returns_none_on_empty_text(self):
        """Returns None when text is empty."""
        criteria = {"code": ""}

        result = _laya.judge_chat_category("", criteria)
        assert result is None

        result = _laya.judge_chat_category(None, criteria)  # type: ignore
        assert result is None

    def test_returns_none_on_empty_criteria(self):
        """Returns None when criteria dict is empty."""
        result = _laya.judge_chat_category("some text", {})
        assert result is None

    def test_returns_none_on_exception(self):
        """Never raises; returns None on any exception."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.side_effect = RuntimeError("Laya error")

            criteria = {"code": ""}
            result = _laya.judge_chat_category("debug", criteria)

            assert result is None

    def test_case_insensitive_choice(self):
        """Handles choice case-insensitively."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = {
                "choice": "CODE",  # uppercase
                "confidence": 0.95,
                "probabilities": {},
            }

            criteria = {"code": "", "tools": ""}
            result = _laya.judge_chat_category("debug", criteria)

            assert result == "code"  # normalized to lowercase


class TestMainConstants:
    """Verify main.py defines required constants."""

    def test_main_defines_laya_judge_id(self):
        """main.py must define LAYA_JUDGE_ID."""
        import ast
        import inspect

        main_file = str(__file__).rsplit("/", 2)[0] + "/main.py"
        with open(main_file) as f:
            tree = ast.parse(f.read())

        constants = {
            node.targets[0].id: node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }

        assert "LAYA_JUDGE_ID" in constants, "main.py must define LAYA_JUDGE_ID"

    def test_main_defines_judge_criteria(self):
        """main.py must define _JUDGE_CRITERIA with all categories."""
        import ast

        main_file = str(__file__).rsplit("/", 2)[0] + "/main.py"
        with open(main_file) as f:
            tree = ast.parse(f.read())

        # Find _JUDGE_CRITERIA assignment (handles both Assign and AnnAssign)
        criteria_dict = None
        for node in ast.walk(tree):
            # Handle annotated assignment: _JUDGE_CRITERIA: dict[str, str] = {...}
            if isinstance(node, ast.AnnAssign):
                if (isinstance(node.target, ast.Name)
                    and node.target.id == "_JUDGE_CRITERIA"
                    and isinstance(node.value, ast.Dict)):
                    criteria_dict = {
                        k.value: None
                        for k in node.value.keys
                        if isinstance(k, ast.Constant)
                    }
            # Handle regular assignment: _JUDGE_CRITERIA = {...}
            elif isinstance(node, ast.Assign):
                if (len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "_JUDGE_CRITERIA"
                    and isinstance(node.value, ast.Dict)):
                    criteria_dict = {
                        k.value: None
                        for k in node.value.keys
                        if isinstance(k, ast.Constant)
                    }

        assert criteria_dict is not None, "main.py must define _JUDGE_CRITERIA"

        # Check that all judge categories are represented
        judge_categories = {
            "trivial", "code", "tools", "reasoning", "short", "default"
        }
        assert set(criteria_dict.keys()) == judge_categories, \
            f"_JUDGE_CRITERIA keys {set(criteria_dict.keys())} != {judge_categories}"

    def test_memory_model_guarded_by_laya_check(self):
        """Memory_model mirror in update_model_roles and wizard must be guarded by LAYA_JUDGE_ID check."""
        import ast

        main_file = str(__file__).rsplit("/", 2)[0] + "/main.py"
        with open(main_file) as f:
            source = f.read()
            tree = ast.parse(source)

        # Find update_model_roles and setup_complete functions
        found_guard_in_update = False
        found_guard_in_setup = False

        for node in ast.walk(tree):
            # Check update_model_roles function
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "update_model_roles":
                func_source = ast.get_source_segment(source, node) or ""
                # Assert that the function source contains both "LAYA_JUDGE_ID" and "memory_model"
                assert "LAYA_JUDGE_ID" in func_source, \
                    "update_model_roles must reference LAYA_JUDGE_ID to guard memory_model mirror"
                assert "memory_model" in func_source, \
                    "update_model_roles must reference memory_model"
                found_guard_in_update = True

            # Check setup_complete handler
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "setup_complete":
                func_source = ast.get_source_segment(source, node) or ""
                # Assert that the function source contains both "LAYA_JUDGE_ID" and "memory_model"
                assert "LAYA_JUDGE_ID" in func_source, \
                    "setup_complete must reference LAYA_JUDGE_ID to guard memory_model mirror"
                assert "memory_model" in func_source, \
                    "setup_complete must reference memory_model"
                found_guard_in_setup = True

        assert found_guard_in_update, "Could not find update_model_roles function"
        assert found_guard_in_setup, "Could not find setup_complete function"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
