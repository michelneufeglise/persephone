"""
Tests for Laya decide_intent and decide_file_role functions.

Tests the document assistant intent classification without importing main.py.
Uses mocking to avoid real model initialization.
"""

import sys
sys.path.insert(0, str(__file__).rsplit("/", 2)[0])

import pytest
from unittest.mock import Mock, patch
import laya_decider as _laya


class TestDecideIntent:
    """Test laya_decider.decide_intent with mocked Laya router."""

    def test_returns_intent_dict_on_success(self):
        """Returns dict with intent, confidence, and probabilities."""
        with patch.object(_laya, 'judge_choice') as mock_choice:
            mock_choice.return_value = {
                "choice": "extract_data",
                "confidence": 0.82,
                "probabilities": {
                    "extract_data": 0.82,
                    "summarize": 0.10,
                    "general_question": 0.08,
                },
            }

            result = _laya.decide_intent(
                "extract tables and amounts",
                [{"name": "invoice.pdf", "kind": "pdf", "chars": 1024}]
            )

            assert result is not None
            assert result["intent"] == "extract_data"
            assert result["confidence"] == 0.82
            assert "probabilities" in result
            assert isinstance(result["probabilities"], dict)

    def test_returns_none_on_empty_message(self):
        """Returns None when message is empty."""
        result = _laya.decide_intent("", [])
        assert result is None

        result = _laya.decide_intent(None, [])
        assert result is None

    def test_returns_none_on_non_string_message(self):
        """Returns None when message is not a string."""
        result = _laya.decide_intent(123, [])
        assert result is None

        result = _laya.decide_intent({"text": "hello"}, [])
        assert result is None

    def test_handles_message_truncation(self):
        """Truncates long messages to ~1500 chars before sending to Laya."""
        long_message = "x" * 3000
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "intent": {
                        "choice": "summarize",
                        "answer_confidence": 0.75,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            result = _laya.decide_intent(long_message, [])

            # Verify router.predict was called
            assert mock_router.predict.called
            # Check that the state text contains the truncated message
            state_arg = mock_router.predict.call_args[1]['state']
            assert len(state_arg) <= 1500 + len("USER REQUEST: ")  # message + header

    def test_includes_file_list_in_state(self):
        """Includes file names and kinds in the state text sent to Laya."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "intent": {
                        "choice": "verify_signature",
                        "answer_confidence": 0.70,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            files = [
                {"name": "contract.pdf", "kind": "pdf"},
                {"name": "signature_ref.png", "kind": "image"},
            ]

            _laya.decide_intent("verify the signature", files)

            state_arg = mock_router.predict.call_args[1]['state']
            assert "contract.pdf" in state_arg
            assert "signature_ref.png" in state_arg
            assert "ATTACHED FILES:" in state_arg

    def test_returns_none_when_router_unavailable(self):
        """Returns None when router is not available."""
        with patch.object(_laya, '_get_or_create_router', return_value=None):
            result = _laya.decide_intent("classify this", [])
            assert result is None

    def test_returns_none_on_exception(self):
        """Never raises; returns None on exception."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router_getter.side_effect = RuntimeError("Router error")

            result = _laya.decide_intent("classify", [])
            assert result is None

    def test_returns_general_question_as_default(self):
        """Returns general_question when choice is missing."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "intent": {
                        "choice": None,
                        "answer_confidence": 0.5,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            result = _laya.decide_intent("something", [])
            assert result is not None
            assert result["intent"] == "general_question"

    def test_all_intents_are_in_criteria(self):
        """All intents from INTENTS constant are passed to Laya."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "intent": {
                        "choice": "summarize",
                        "answer_confidence": 0.75,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            _laya.decide_intent("summarize the doc", [])

            # Check that judge_choice received all intents
            questions = mock_router.predict.call_args[1]['questions']
            criteria = questions['intent']['criteria']

            expected_intents = set(_laya.INTENTS.keys())
            actual_intents = set(criteria.keys())
            assert actual_intents == expected_intents


class TestDecideFileRole:
    """Test laya_decider.decide_file_role with mocked Laya router."""

    def test_returns_role_dict_on_success(self):
        """Returns dict with role, confidence, and probabilities."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "role": {
                        "choice": "subject_document",
                        "answer_confidence": 0.88,
                        "probabilities": {
                            "subject_document": 0.88,
                            "reference_specimen": 0.12,
                        },
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            result = _laya.decide_file_role(
                "analyze this contract",
                {"name": "contract.pdf", "kind": "pdf"}
            )

            assert result is not None
            assert result["role"] in ["subject_document", "reference_specimen"]
            assert result["confidence"] == 0.88
            assert "probabilities" in result

    def test_returns_none_on_empty_message(self):
        """Returns None when message is empty."""
        file = {"name": "test.pdf", "kind": "pdf"}

        result = _laya.decide_file_role("", file)
        assert result is None

        result = _laya.decide_file_role(None, file)
        assert result is None

    def test_returns_none_on_missing_file(self):
        """Returns None when file is None or empty."""
        result = _laya.decide_file_role("message", None)
        assert result is None

        result = _laya.decide_file_role("message", {})
        assert result is None

    def test_includes_file_snippet_in_state(self):
        """Includes file snippet in state when provided."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "role": {
                        "choice": "reference_specimen",
                        "answer_confidence": 0.75,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            file = {
                "name": "signature_card.png",
                "kind": "image",
                "snippet": "This is a signature reference card with sample signatures",
            }

            _laya.decide_file_role("compare against this", file)

            state_arg = mock_router.predict.call_args[1]['state']
            assert "signature_card.png" in state_arg
            assert "reference" in state_arg.lower() or "FIRST TEXT:" in state_arg

    def test_truncates_long_message(self):
        """Truncates long messages to ~1500 chars."""
        long_message = "x" * 3000
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "role": {
                        "choice": "subject_document",
                        "answer_confidence": 0.70,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            _laya.decide_file_role(long_message, {"name": "file.pdf", "kind": "pdf"})

            state_arg = mock_router.predict.call_args[1]['state']
            # Message should be truncated to 1500 chars plus file metadata
            assert len(state_arg) < 2000  # Allow room for message + file info + newlines

    def test_returns_none_when_router_unavailable(self):
        """Returns None when router is not available."""
        with patch.object(_laya, '_get_or_create_router', return_value=None):
            result = _laya.decide_file_role(
                "message",
                {"name": "file.pdf", "kind": "pdf"}
            )
            assert result is None

    def test_returns_none_on_exception(self):
        """Never raises; returns None on exception."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router_getter.side_effect = RuntimeError("Router error")

            result = _laya.decide_file_role(
                "message",
                {"name": "file.pdf", "kind": "pdf"}
            )
            assert result is None

    def test_returns_subject_document_as_default(self):
        """Returns subject_document when choice is missing."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "role": {
                        "choice": None,
                        "answer_confidence": 0.5,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            result = _laya.decide_file_role(
                "message",
                {"name": "file.pdf", "kind": "pdf"}
            )
            assert result is not None
            assert result["role"] == "subject_document"

    def test_role_criteria_matches_subject_and_reference(self):
        """Role classification includes both subject_document and reference_specimen."""
        with patch.object(_laya, '_get_or_create_router') as mock_router_getter:
            mock_router = Mock()
            mock_router.predict.return_value = {
                "answers": {
                    "role": {
                        "choice": "reference_specimen",
                        "answer_confidence": 0.85,
                        "probabilities": {},
                    }
                }
            }
            mock_router_getter.return_value = mock_router

            _laya.decide_file_role(
                "verify against",
                {"name": "ref.png", "kind": "image"}
            )

            questions = mock_router.predict.call_args[1]['questions']
            criteria = questions['role']['criteria']

            assert "subject_document" in criteria
            assert "reference_specimen" in criteria
            assert len(criteria) == 2


class TestIntentsConstant:
    """Test the INTENTS constant is properly defined."""

    def test_intents_is_ordered_dict(self):
        """INTENTS is an OrderedDict."""
        from collections import OrderedDict
        assert isinstance(_laya.INTENTS, OrderedDict)

    def test_intents_has_all_required_categories(self):
        """INTENTS contains all 7 expected intent categories."""
        expected = {
            "verify_signature",
            "identify_person",
            "summarize",
            "extract_data",
            "translate",
            "redact",
            "general_question",
        }
        assert set(_laya.INTENTS.keys()) == expected

    def test_intents_have_non_empty_descriptions(self):
        """Each intent has a non-empty description string."""
        for intent_name, description in _laya.INTENTS.items():
            assert isinstance(description, str)
            assert len(description) > 0
            assert len(description) < 500  # Reasonable limit for criteria description

    def test_intent_descriptions_are_distinctive(self):
        """Intent descriptions are sufficiently different from each other."""
        descriptions = list(_laya.INTENTS.values())
        # Check that descriptions don't just repeat the same text
        unique_descs = set(descriptions)
        assert len(unique_descs) == len(descriptions), "Intent descriptions should be unique"


class TestConfidenceConstants:
    """Test confidence threshold constants are properly defined."""

    def test_intent_min_confidence_is_set(self):
        """LAYA_INTENT_MIN_CONFIDENCE is defined and is a float in [0, 1]."""
        assert hasattr(_laya, 'LAYA_INTENT_MIN_CONFIDENCE')
        assert 0 <= _laya.LAYA_INTENT_MIN_CONFIDENCE <= 1

    def test_role_min_confidence_is_set(self):
        """LAYA_ROLE_MIN_CONFIDENCE is defined and is a float in [0, 1]."""
        assert hasattr(_laya, 'LAYA_ROLE_MIN_CONFIDENCE')
        assert 0 <= _laya.LAYA_ROLE_MIN_CONFIDENCE <= 1

    def test_intent_confidence_lower_than_role(self):
        """Intent confidence threshold is lower than role (role requires higher confidence)."""
        assert _laya.LAYA_INTENT_MIN_CONFIDENCE < _laya.LAYA_ROLE_MIN_CONFIDENCE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
