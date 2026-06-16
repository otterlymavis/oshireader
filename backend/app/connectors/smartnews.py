from __future__ import annotations

from app.connectors.news_sites import _GNewsSiteConnector


class SmartNewsConnector(_GNewsSiteConnector):
    """SmartNews has no public search/RSS endpoint, so fetch via Google News
    filtered to smartnews.com (same pattern as the other site connectors)."""

    PLATFORM = "smartnews"
    SITE = "smartnews.com"
