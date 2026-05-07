"""Tests for __main__.py — checksum verification and model load guard."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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

        with (
            patch("github_bounty_scraper.__main__.run_pipeline") as mock_pipeline,
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
        ):
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

        with (
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
            patch("github_bounty_scraper.vibe.run_vibe_check") as mock_vibe,
        ):
            mock_parse.return_value = ("vibe-check", mock_ns, cfg)

            main()

        mock_vibe.assert_called_once()

    def test_main_dump_dataset_dispatch(self, cfg):
        mock_ns = MagicMock()
        mock_ns.db_path = "bounty.db"
        mock_ns.out = "dataset.csv"
        mock_ns.raw_file = "raw.jsonl"
        mock_ns.label_threshold = 25.0

        with (
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
            patch("github_bounty_scraper.db.dump_dataset") as mock_dump,
        ):
            mock_parse.return_value = ("dump-dataset", mock_ns, cfg)
            main()

        mock_dump.assert_called_once()

    def test_main_scrape_with_auto_refresh_fresh(self, cfg):
        mock_ns = MagicMock()
        mock_ns.auto_refresh = True
        mock_ns.db_path = "bounty.db"
        mock_ns.refresh_days = 3

        import time

        now = time.time()

        with (
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
            patch("sqlite3.connect") as mock_conn_cls,
            patch("sys.exit") as mock_exit,
        ):
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (now - 3600,)  # 1 hour old

            mock_parse.return_value = ("scrape", mock_ns, cfg)
            main()
            mock_exit.assert_called_with(0)

    def test_main_scrape_with_auto_refresh_stale(self, cfg):
        mock_ns = MagicMock()
        mock_ns.auto_refresh = True
        mock_ns.db_path = "bounty.db"
        mock_ns.refresh_days = 3

        import time

        now = time.time()

        with (
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
            patch("sqlite3.connect") as mock_conn_cls,
            patch("github_bounty_scraper.__main__.run_pipeline") as mock_pipeline,
        ):
            mock_conn = MagicMock()
            mock_conn_cls.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (now - 10 * 86400,)  # 10 days old

            mock_parse.return_value = ("scrape", mock_ns, cfg)
            main()
            mock_pipeline.assert_called_once()

    def test_main_inspect_leads_dispatch(self, cfg):
        mock_ns = MagicMock()
        mock_ns.db_path = "bounty.db"
        mock_ns.mode = "strict"
        mock_ns.limit = 5

        with (
            patch("github_bounty_scraper.__main__.parse_args") as mock_parse,
            patch("github_bounty_scraper.__main__._run_inspect") as mock_inspect,
        ):
            mock_parse.return_value = ("inspect-leads", mock_ns, cfg)
            main()

        mock_inspect.assert_called_once()


class TestModelLoadAndInspect:
    @pytest.mark.asyncio
    async def test_run_inspect_leakage_fatal(self):
        """If best_threshold.json says leakage_free=False, exit 1."""
        from github_bounty_scraper.__main__ import _run_inspect
        
        with patch("os.path.exists", side_effect=lambda p: p in ["best_threshold.json", "bounty_model.pkl"]), \
             patch("builtins.open", MagicMock()), \
             patch("json.load", return_value={"leakage_free": False}), \
             patch("sys.exit") as mock_exit:
            
            await _run_inspect("db.db", "strict", 10)
            mock_exit.assert_any_call(1)

    @pytest.mark.asyncio
    async def test_run_inspect_with_model_success(self):
        """Verify model load and inference path."""
        from github_bounty_scraper.__main__ import _run_inspect
        
        leads = [{
            "score": 80.0,
            "repo_name": "owner/repo",
            "issue_url": "https://url",
            "numeric_amount": 100.0,
            "vibe_score": 75,
            "positive_escrow_count": 2,
            "escrow_weight_sum": 3.0,
            "has_onchain_escrow": 1,
            "mentions_no_kyc": 0,
            "mentions_wallet_payout": 1,
            "merges_last_45d": 10,
            "lead_mode": "strict"
        }]
        
        import numpy as np
        mock_model = MagicMock()
        mock_model.predict_proba.return_value = np.array([[0.1, 0.9]]) # 90% prob
        mock_joblib = MagicMock()
        mock_joblib.load.return_value = mock_model
        
        # Patch exists to return True for the model file
        with patch.dict("sys.modules", {"joblib": mock_joblib}), \
             patch("github_bounty_scraper.db.get_recent_leads", AsyncMock(return_value=leads)), \
             patch("os.path.exists", return_value=True), \
             patch("builtins.open", MagicMock()), \
             patch("json.load", return_value={"leakage_free": True}), \
             patch("github_bounty_scraper.__main__._verify_model_checksum"):
            
            await _run_inspect("db.db", "strict", 1)
            
        assert leads[0]["ml_prob"] == 0.9
        mock_model.predict_proba.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_inspect_model_checksum_fail_swallowed(self):
        """A checksum failure in _run_inspect is swallowed (model remains None)."""
        from github_bounty_scraper.__main__ import _run_inspect
        
        leads = [{"score": 50.0, "repo_name": "test", "issue_url": "url", "numeric_amount": 0.0}]
        
        with patch("github_bounty_scraper.db.get_recent_leads", AsyncMock(return_value=leads)), \
             patch("os.path.exists", side_effect=lambda p: p == "bounty_model.pkl"), \
             patch("github_bounty_scraper.__main__._verify_model_checksum", side_effect=RuntimeError("bad hash")), \
             patch("joblib.load") as mock_load:
            
            await _run_inspect("db.db", "strict", 1)
            
        mock_load.assert_not_called()
        assert leads[0]["ml_prob"] == 0.0

    @pytest.mark.asyncio
    async def test_run_inspect_inference_exception_swallowed(self):
        """An inference exception is swallowed per-lead."""
        from github_bounty_scraper.__main__ import _run_inspect
        
        leads = [{"score": 50.0, "repo_name": "test", "issue_url": "url", "numeric_amount": 0.0}]
        mock_model = MagicMock()
        mock_model.predict_proba.side_effect = Exception("infer fail")
        
        with patch("github_bounty_scraper.db.get_recent_leads", AsyncMock(return_value=leads)), \
             patch("os.path.exists", side_effect=lambda p: p == "bounty_model.pkl"), \
             patch("github_bounty_scraper.__main__._verify_model_checksum"), \
             patch("joblib.load", return_value=mock_model):
            
            await _run_inspect("db.db", "strict", 1)
            
        assert leads[0]["ml_prob"] == 0.0
