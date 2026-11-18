"""Markdownから本文の検査単位と原文位置を取り出す。"""
from copy import deepcopy
import re
import unicodedata

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdown_it import MarkdownIt

SOURCE_ATTR = "data-doccheck-line"
SKIP = {"pre", "script", "style", "template", "noscript", "svg", "math", "img", "blockquote"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def visible_text(node, own_item=False):
    """表示文と、インラインコードを伏せた同じ長さの検査文を返す。"""
    chunks = []

    def walk(el, root=False, code=False):
        if isinstance(el, Comment):
            return
        if isinstance(el, NavigableString):
            value = re.sub(r"[\t\r\f ]+", " ", str(el))
            chunks.extend((c, code) for c in value)
            return
        if not isinstance(el, Tag) or el.name in SKIP | HEADINGS or el.has_attr("hidden"):
            return
        if el.name == "br" or (not root and own_item and el.name in {"ul", "ol", "table"}):
            chunks.append(("\u2028", False))
            return
        block = not root and el.name in {"p", "div", "li"}
        if block:
            chunks.append(("\u2028", False))
        for child in el.children:
            walk(child, code=code or el.name == "code")
        if block:
            chunks.append(("\u2028", False))

    walk(node, root=True)
    rendered, masked = [], []
    i = 0
    while i < len(chunks):
        char, protected = chunks[i]
        if char == "\u2028":
            if rendered and rendered[-1] != "\n":
                rendered.append("\n")
                masked.append("\n")
            i += 1
            continue
        if char.isspace():
            j = i + 1
            while j < len(chunks) and chunks[j][0].isspace() and chunks[j][0] != "\u2028":
                j += 1
            before = rendered[-1] if rendered else ""
            after = chunks[j][0] if j < len(chunks) else ""
            soft = any(c == "\n" for c, _ in chunks[i:j])
            japanese = before and after and all(unicodedata.east_asian_width(c) in {"W", "F"} for c in (before, after))
            if before and after and before != "\n" and after != "\u2028" and not (soft and japanese):
                rendered.append(" ")
                masked.append(" ")
            i = j
            continue
        rendered.append(char)
        masked.append("\ufffc" if protected else char)
        i += 1
    return "".join(rendered).strip(), "".join(masked).strip()


def extract_units(text):
    """HTMLの段落・表セル・箇条書き項目を、Markdownの開始行とともに返す。"""
    md = MarkdownIt("commonmark", {"html": True, "breaks": False}).enable("table")
    front = re.match(r"\A---[ \t]*\n.*?^(?:---|\.\.\.)[ \t]*(?:\n|\Z)", text, re.S | re.M)
    parse_text = text
    if front:
        parse_text = "".join("\n" if c == "\n" else " " for c in front[0]) + text[front.end():]
    tokens = md.parse(parse_text)
    stack = []
    for token in tokens:
        bounds = token.map or next((b for b in reversed(stack) if b), None)
        if token.nesting == 1:
            stack.append(bounds)
        elif token.nesting == -1 and stack:
            stack.pop()
        if not bounds or token.type not in {"paragraph_open", "list_item_open", "td_open", "th_open", "html_block"}:
            continue
        line = str(bounds[0] + 1)
        if token.type == "html_block":
            token.content = f'<div {SOURCE_ATTR}="{line}">' + token.content + "</div>\n"
        else:
            token.attrSet(SOURCE_ATTR, line)
    soup = BeautifulSoup(md.renderer.render(tokens, md.options, {}), "html.parser")
    raw = []
    # 生HTMLの裸の本文も拾い、内部の段落・表・リストとの二重検出を避ける。
    for node in soup.select(f"div[{SOURCE_ATTR}]"):
        own = deepcopy(node)
        for child in list(own.find_all(["p", "ul", "ol", "table"])):
            if child.parent is not None:
                child.replace_with(soup.new_tag("br"))
        raw.append(own)
    units = []
    for node in list(soup.find_all(["p", "li", "td", "th"])) + raw:
        if any(p.name in SKIP | HEADINGS or p.has_attr("hidden") for p in [node, *node.parents] if isinstance(p, Tag)):
            continue
        if node.name == "p" and node.find_parent(["li", "td", "th"]):
            continue
        owner = node if node.has_attr(SOURCE_ATTR) else node.find_parent(attrs={SOURCE_ATTR: True})
        if owner is None:
            continue
        kind = "list_item" if node.name == "li" else "table_cell" if node.name in {"td", "th"} else "paragraph"
        display, masked = visible_text(node, own_item=kind == "list_item")
        if not display:
            continue
        outside_links = "".join(str(x) for x in node.descendants if isinstance(x, NavigableString)
                                and not isinstance(x, Comment) and x.find_parent("a") is None)
        label = re.sub(r"\s|[。！？]", "", outside_links)
        link_only = bool(node.find("a")) and label in {"", "設計:", "設計：", "参照:", "参照：", "関連:", "関連："}
        units.append({"line": int(owner[SOURCE_ATTR]), "kind": kind, "display": display,
                      "masked": masked, "link_only": link_only})
    return sorted(units, key=lambda u: u["line"])
