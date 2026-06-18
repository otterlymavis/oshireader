from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup, Tag

try:
    from scrapling import Selector
except ImportError:  # pragma: no cover - exercised implicitly on Python 3.9 local envs
    Selector = None


class _SoupSelector:
    def __init__(self, node: BeautifulSoup | Tag) -> None:
        self._node = node
        self.tag = getattr(node, "name", None)
        self.attrib = getattr(node, "attrs", {})

    def css(self, selector: str) -> list[_SoupSelector]:
        return [_SoupSelector(node) for node in self._node.select(selector)]

    def find_ancestor(self, predicate: Any) -> _SoupSelector | None:
        for parent in getattr(self._node, "parents", []):
            if not getattr(parent, "name", None):
                continue
            wrapped = _SoupSelector(parent)
            if predicate(wrapped):
                return wrapped
        return None

    def get_all_text(self, separator: str = " ", strip: bool = True) -> str:
        return self._node.get_text(separator, strip=strip)


def scrapling_page(html: str, url: str) -> Any:
    if Selector is not None:
        return Selector(html or "", url=url)
    return _SoupSelector(BeautifulSoup(html or "", "lxml"))


def text_of(node: Any | None) -> str:
    if node is None:
        return ""
    try:
        value = node.get_all_text(separator=" ", strip=True)
    except TypeError:
        value = node.get_all_text(" ", True)
    except AttributeError:
        value = getattr(node, "text", "")
    return str(value or "").strip()


def attr_of(node: Any | None, name: str) -> str:
    if node is None:
        return ""
    try:
        value = node.attrib.get(name)
    except AttributeError:
        value = ""
    return str(value or "").strip()


def first(nodes: Any) -> Any | None:
    return nodes[0] if nodes else None
