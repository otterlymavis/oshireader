"""Tests for the Settings.cors_origins computed property."""
from __future__ import annotations

from unittest.mock import patch

from app.config import Settings


class TestCorsOrigins:
    def _settings(self, value: str) -> Settings:
        return Settings(cors_allow_origins=value)

    def test_empty_string_returns_empty_list(self):
        s = self._settings("")
        assert s.cors_origins == []

    def test_single_origin(self):
        s = self._settings("https://example.com")
        assert s.cors_origins == ["https://example.com"]

    def test_multiple_comma_separated(self):
        s = self._settings("https://a.com,https://b.com,https://c.com")
        assert s.cors_origins == ["https://a.com", "https://b.com", "https://c.com"]

    def test_strips_whitespace_around_entries(self):
        s = self._settings("  https://a.com  ,  https://b.com  ")
        assert s.cors_origins == ["https://a.com", "https://b.com"]

    def test_filters_blank_entries(self):
        s = self._settings("https://a.com,,https://b.com")
        assert s.cors_origins == ["https://a.com", "https://b.com"]

    def test_whitespace_only_value_returns_empty_list(self):
        s = self._settings("   ")
        assert s.cors_origins == []
