"""
Tests for laya_decider module.

Unit tests use mocked laya imports; integration test requires actual Laya installation.
"""

import pytest
import sys
import os
from unittest.mock import patch, MagicMock
from typing import Any


class TestLayaDeciderUnit:
    """Unit tests with mocked laya (no actual model loading)."""

    def test_status_when_not_loaded(self):
        """status() returns correct shape when router is not loaded."""
        import laya_decider

        # Reset state
        laya_decider._router_instance = None

        with patch.object(laya_decider, 'is_available', return_value=False):
            result = laya_decider.status()

            assert isinstance(result, dict)
            assert "available" in result
            assert "loaded" in result
            assert "device" in result
            assert result["available"] is False
            assert result["loaded"] is False
            assert result["device"] is None

    def test_status_shape(self):
        """status() returns correct dict structure."""
        import laya_decider
        laya_decider._router_instance = None

        result = laya_decider.status()
        assert set(result.keys()) == {"available", "loaded", "device"}
        assert isinstance(result["available"], bool)
        assert isinstance(result["loaded"], bool)
        assert result["device"] is None or isinstance(result["device"], str)

    def test_is_available_returns_bool(self):
        """is_available() always returns a bool."""
        import laya_decider
        result = laya_decider.is_available()
        assert isinstance(result, bool)

    def test_decide_returns_none_on_empty_text(self):
        """decide() returns None for empty input."""
        import laya_decider
        laya_decider._router_instance = None

        with patch.object(laya_decider, '_get_or_create_router', return_value=None):
            result = laya_decider.decide("")
            assert result is None

    def test_decide_returns_none_on_unavailable(self):
        """decide() returns None when router is unavailable."""
        import laya_decider
        laya_decider._router_instance = None

        with patch.object(laya_decider, '_get_or_create_router', return_value=None):
            result = laya_decider.decide("Some text")
            assert result is None

    def test_decide_truncates_input(self):
        """decide() truncates long inputs."""
        import laya_decider

        # Create a mock router
        mock_router = MagicMock()
        mock_result = {
            "answers": {
                "kind": {
                    "choice": "plain_note",
                    "answer_confidence": 0.95,
                    "probabilities": {"plain_note": 0.95},
                },
                "complexity": {
                    "score": 0.5,
                },
            }
        }
        mock_router.predict.return_value = mock_result

        laya_decider._router_instance = None

        # Mock the router creation
        with patch.object(laya_decider, '_get_or_create_router', return_value=mock_router):
            long_text = "x" * 100000
            result = laya_decider.decide(long_text)

            # Check that predict was called with truncated text
            assert mock_router.predict.called
            call_args = mock_router.predict.call_args
            state = call_args[1]["state"]
            assert len(state) <= laya_decider.LAYA_TRUNCATE_LENGTH

            # Check result structure
            assert result is not None
            assert "kind" in result
            assert "confidence" in result
            assert "complexity" in result

    def test_decide_result_structure(self):
        """decide() returns correct result structure."""
        import laya_decider

        # Mock router
        mock_router = MagicMock()
        mock_result = {
            "answers": {
                "kind": {
                    "choice": "email",
                    "answer_confidence": 0.92,
                    "probabilities": {
                        "email": 0.92,
                        "invoice_or_form": 0.05,
                        "table_heavy": 0.01,
                        "long_report_or_contract": 0.01,
                        "plain_note": 0.01,
                    },
                },
                "complexity": {
                    "score": 1.2,
                },
            }
        }
        mock_router.predict.return_value = mock_result

        laya_decider._router_instance = None

        with patch.object(laya_decider, '_get_or_create_router', return_value=mock_router):
            result = laya_decider.decide("Sample email text")

            assert isinstance(result, dict)
            assert set(result.keys()) == {"kind", "confidence", "probabilities", "complexity"}

            # Check types
            assert isinstance(result["kind"], str)
            assert isinstance(result["confidence"], float)
            assert isinstance(result["probabilities"], dict)
            assert isinstance(result["complexity"], int)

            # Check values
            assert result["kind"] == "email"
            assert 0 <= result["confidence"] <= 1
            assert result["complexity"] in {0, 1, 2}

    def test_decide_never_raises(self):
        """decide() never raises, even on errors."""
        import laya_decider

        laya_decider._router_instance = None

        with patch.object(laya_decider, '_get_or_create_router', side_effect=RuntimeError("Test error")):
            # Should not raise
            result = laya_decider.decide("Some text")
            assert result is None

    def test_ensure_downloaded_returns_bool(self):
        """ensure_downloaded() returns a bool."""
        import laya_decider

        with patch.object(laya_decider, 'is_available', return_value=False):
            with patch('huggingface_hub.snapshot_download', side_effect=Exception("No internet")):
                result = laya_decider.ensure_downloaded()
                assert isinstance(result, bool)

    def test_truncation_length_constant(self):
        """LAYA_TRUNCATE_LENGTH is a reasonable value."""
        import laya_decider
        assert laya_decider.LAYA_TRUNCATE_LENGTH > 100
        assert laya_decider.LAYA_TRUNCATE_LENGTH < 100000


class TestLayaDeciderIntegration:
    """Integration tests requiring actual Laya installation.

    Skipped unless laya_decider.is_available() returns True.
    """

    @pytest.mark.skipif(
        not __import__('laya_decider').is_available(),
        reason="Laya not available; run ensure_downloaded() first"
    )
    def test_decide_on_support_email(self):
        """decide() classifies a support email correctly."""
        import laya_decider

        email_text = """
        Subject: Help with Invoice #12345

        Hi Support,

        I received invoice #12345 but the total amount seems incorrect.
        Can you please review and confirm the breakdown?

        Best regards,
        John Doe
        john@example.com
        """

        result = laya_decider.decide(email_text)

        # Should not be None
        assert result is not None

        # Check structure
        assert "kind" in result
        assert "confidence" in result
        assert "complexity" in result
        assert "probabilities" in result

        # Email should be detected (or at least be one of the top options)
        assert result["kind"] in [
            "email",
            "invoice_or_form",  # borderline, as email contains invoice reference
            "plain_note",
        ]

        # Confidence should be reasonable
        assert 0 <= result["confidence"] <= 1

        # Complexity should be 0-2
        assert result["complexity"] in {0, 1, 2}
