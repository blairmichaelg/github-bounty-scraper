"""Tests for __main__.py — checksum verification and model load guard."""

from __future__ import annotations

import hashlib

import pytest

from github_bounty_scraper.__main__ import _verify_model_checksum


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
