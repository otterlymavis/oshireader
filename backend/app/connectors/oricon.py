from __future__ import annotations

import logging
import re

from app.connectors.base import GoogleNewsDirectAndBingConnector

log = logging.getLogger(__name__)

_SUFFIX_RE = re.compile(r"\s*[-|]\s*(ORICON NEWS|オリコンニュース|オリコン)\s*$", re.I)


def _clean_title(value: str) -> str:
    return _SUFFIX_RE.sub("", value).strip()


class OriconConnector(GoogleNewsDirectAndBingConnector):
    PLATFORM = "oricon"
    SITE = "oricon.co.jp"
    TITLE_SUFFIX_RE = _SUFFIX_RE
    AUTHOR = "ORICON NEWS"
    ITEM_CAP = 20
