from __future__ import annotations

import logging
import re

from app.connectors.base import GoogleNewsDirectAndBingConnector

log = logging.getLogger(__name__)

_SUFFIX_RE = re.compile(r"\s*[-|]\s*モデルプレス\s*$", re.I)


def _clean_title(value: str) -> str:
    return _SUFFIX_RE.sub("", value).strip()


class ModelPressConnector(GoogleNewsDirectAndBingConnector):
    PLATFORM = "mdpr"
    SITE = "mdpr.jp"
    TITLE_SUFFIX_RE = _SUFFIX_RE
    ITEM_CAP = 25
