"""Tests for __main__.py — checksum verification and model load guard."""

from __future__ import annotations

import hashlib

import pytest
from unittest.mock import MagicMock, patch

from github_bounty_scraper.__main__ import _verify_model_checksum, main


class TestVerifyModelChecksum:
    def test_valid_checksum_passes(self, tmp_path):
        model_file = tmp_path / "model.pkl"
        model_file.write_bytes(b"fake model content")
        digest = hashlib.sha256(b"fake model content").hexdigest()
        sidecar = tmp_path / "model.pkl.sha256"
        sidecar.write_text(digest + "\n")
        # Should not raise
        _verify_model_checksum(str(model_file), str(sidecar))

    def test_tampered_model_raises(self, tmp_path):
        model_file = tmp_path / "model.pkl"
        model_file.write_bytes(b"original content")
        digest = hashlib.sha256(b"original content").hexdigest()
        sidecar = tmp_path / "model.pkl.sha256"
        sidecar.write_text(digest + "\n")
        # Tamper with the model
        model_file.write_bytes(b"TAMPERED CONTENT")
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            _verify_model_checksum(str(model_file), str(sidecar))

    def test_missing_sidecar_raises(self, tmp_path):
        model_file = tmp_path / "model.pkl"
        model_file.write_bytes(b"content")
        with pytest.raises((FileNotFoundError, RuntimeError)):
            _verify_model_checksum(str(model_file), str(tmp_path / "nonexistent.sha256"))

    def test_missing_model_raises(self, tmp_path):
        sidecar = tmp_path / "model.pkl.sha256"
        sidecar.write_text("abc123\n")
        with pytest.raises((FileNotFoundError, RuntimeError)):
            _verify_model_checksum(str(tmp_path / "nonexistent.pkl"), str(sidecar))

    def test_checksum_with_extra_whitespace(self, tmp_path):
        """Sidecar file may have trailing newline or spaces — should still match."""
        model_file = tmp_path / "model.pkl"
        model_file.write_bytes(b"data")
        digest = hashlib.sha256(b"data").hexdigest()
        sidecar = tmp_path / "model.pkl.sha256"
        sidecar.write_text(f"  {digest}  \n")
        _verify_model_checksum(str(model_file), str(sidecar))

    def test_sha256_format_with_filename(self, tmp_path):
        """sha256sum output format: 'hash  filename' — both parts present."""
        model_file = tmp_path / "model.pkl"
        model_file.write_bytes(b"data")
        digest = hashlib.sha256(b"data").hexdigest()
        sidecar = tmp_path / "model.pkl.sha256"
        sidecar.write_text(f"{digest}  model.pkl\n")
        _verify_model_checksum(str(model_file), str(sidecar))

# === Section 2: main() CLI dispatch ===

class TestMainDispatch:
    """Test that main() correctly dispatches to subcommands."""

    def test_main_calls_run_pipeline_with_config(self, cfg):
        """main() should call run_pipeline exactly once with a ScraperConfig."""
        mock_ns = MagicMock()
        mock_ns.auto_refresh = False

        with patch("github_bounty_scraper.__main__.run_pipeline") as mock_pipeline, \
             patch("github_bounty_scraper.__main__.parse_args") as mock_parse:

            mock_parse.return_value = ("scrape", mock_ns, cfg)
            
            # main() in __main__.py is synchronous
            main()

        mock_pipeline.assert_called_once_with(cfg)

    def test_main_vibe_check_dispatch(self, cfg):
        """main() should call run_vibe_check when subcommand is vibe-check."""
        mock_ns = MagicMock()
        mock_ns.raw_file = "raw.jsonl"
        mock_ns.db_path = "bounty.db"
        mock_ns.limit = 10
        mock_ns.mode = "opportunistic"
        mock_ns.concurrency = 5

        with patch("github_bounty_scraper.__main__.parse_args") as mock_parse, \
             patch("github_bounty_scraper.vibe.run_vibe_check") as mock_vibe:

            mock_parse.return_value = ("vibe-check", mock_ns, cfg)
            
            main()

        mock_vibe.assert_called_once()
