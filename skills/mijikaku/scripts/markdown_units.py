"""設計書のMarkdownから段落・箇条書き・表セルと原文位置を取り出す。"""
from dataclasses import dataclass, field
from html import escape, unescape
from html.parser import HTMLParser
import re
import string
import unicodedata


SOURCE_ATTR = "data-doccheck-line"
SKIP = {"pre", "script", "style", "template", "noscript", "svg", "math", "img", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6"}
UNIT_TYPES = {"li": "list_item", "td": "table_cell", "th": "table_cell", "p": "paragraph"}
CONTAINERS = {"ul", "ol", "table"}
LINK_LABELS = {"", "設計:", "設計：", "参照:", "参照：", "関連:", "関連："}
BREAK = "\u2028"


class Element:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []


class HTMLTree(HTMLParser):
    void = set("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self):
        super().__init__()
        self.root = Element("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        child = Element(tag, attrs)
        self.stack[-1].children.append(child)
        if tag not in self.void:
            self.stack.append(child)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def read_unit(node, kind, raw):
    """表示文字とコードのマスク位置を揃え、リンクだけの案内を識別する。"""
    chunks, outside = [], []
    has_link = False

    def collect(child, code=False, anchor=False, visible=True):
        nonlocal has_link
        if isinstance(child, str):
            if not anchor:
                outside.append(child)
            if visible:
                chunks.extend((c, code) for c in child)
            return
        tag = child.tag
        if raw and tag in CONTAINERS | {"p"}:
            chunks.append((BREAK, False))
            return
        has_link |= tag == "a"
        visible = visible and tag not in SKIP and "hidden" not in child.attrs
        if visible and (tag == "br" or kind in {"list_item", "table_cell"} and tag in CONTAINERS):
            chunks.append((BREAK, False))
            return
        block = visible and child is not node and tag in {"p", "div", "li"}
        if block:
            chunks.append((BREAK, False))
        for item in child.children:
            collect(item, code or tag == "code", anchor or tag == "a", visible)
        if block:
            chunks.append((BREAK, False))

    collect(node)
    display, masked = normalize(chunks)
    link_only = has_link and re.sub(r"\s|[。！？]", "", "".join(outside)) in LINK_LABELS
    return display, masked, link_only


def html_units(markup):
    tree = HTMLTree()
    tree.feed(markup)
    tree.close()
    units, raw_units = [], []

    def visit(node, in_item=False, line=0):
        if isinstance(node, str) or node.tag in SKIP or "hidden" in node.attrs:
            return
        line = int(node.attrs.get(SOURCE_ATTR, line))
        raw = node.tag == "div" and SOURCE_ATTR in node.attrs
        kind = UNIT_TYPES.get(node.tag)
        if raw or kind and not (node.tag == "p" and in_item):
            kind = kind or "paragraph"
            display, masked, link_only = read_unit(node, kind, raw)
            if display:
                target = raw_units if raw else units
                target.append(dict(line=line, kind=kind, display=display, masked=masked, link_only=link_only))
        for child in node.children:
            visit(child, in_item or node.tag in {"li", "th", "td"}, line)

    visit(tree.root)
    return sorted(units + raw_units, key=lambda unit: unit["line"])


def normalize(chunks):
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


ESCAPED = r"\\[" + re.escape(string.punctuation) + r"]"
LABEL = r"\[((?:\\[^\n]|[^\[\]\\]){1,999})\]"
TITLE = r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))'''
TITLE_RE = re.compile(TITLE)
DEFINITION = re.compile(r" {0,3}" + LABEL + r":[ \t]*(?:\n[ \t]*)?")
ENTITY = r"&(?:#[xX][0-9a-fA-F]{1,6}|#[0-9]{1,7}|[a-zA-Z][a-zA-Z0-9]{1,31});"
AUTO = r"<(?:[A-Za-z][A-Za-z0-9.+-]{1,31}:[^\s<>]*|[\w.+-]+@[\w.-]+\.[\w-]+)>"
TAG = r"""</?[A-Za-z][\w-]*(?:\s+[^\s"'=<>/\x00-\x1f]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*\s*/?>"""
TOKEN = re.compile("|".join((
    r"(?P<breakline> {2,}\n|\\\n)", rf"(?P<escaped>{ESCAPED})", r"(?P<code>`+)",
    r"(?P<bracket>!?\[|\])", r"(?P<emphasis>\*+|_+)", rf"(?P<auto>{AUTO})",
    rf"(?P<html><!--[\s\S]*?-->|{TAG})", rf"(?P<entity>{ENTITY})",
)))


def reference_key(label):
    return " ".join(label.split()).casefold()


def destination(text, start):
    """リンク先の末尾を返す。丸括弧は対応する閉じ括弧まで読む。"""
    if text.startswith("<", start):
        match = re.compile(r"<(?:\\.|[^\\<>\n])*>").match(text, start)
        return match.end() if match else None
    depth = 0
    pos = start
    while pos < len(text):
        char = text[pos]
        if char.isspace() or ord(char) < 32 or char == ")" and depth == 0:
            break
        if char == "\\" and pos + 1 < len(text) and text[pos+1] in string.punctuation:
            pos += 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        pos += 1
    return None if depth else pos


def allowed_destination(url):
    url = unescape(re.sub(ESCAPED, lambda m: m[0][1], url.strip("<>"))).lower()
    return not url.startswith(("javascript:", "vbscript:", "file:", "data:")) or bool(
        re.match(r"data:image/(?:gif|png|jpeg|webp);", url))


def definition(text):
    """段落の先頭にある参照定義を読み、ラベルと次の本文位置を返す。"""
    match = DEFINITION.match(text)
    if not match:
        return None
    start = match.end()
    end = destination(text, start)
    if end is None or end == start or not allowed_destination(text[start:end]):
        return None
    rest = text[end:]
    title = re.match(r"[ \t]*(?:\n[ \t]*)?" + TITLE + r"[ \t]*(?:\n|$)", rest)
    if rest[:1].isspace() and title:
        end += title.end()
    else:
        newline = re.match(r"[ \t]*(?:\n|$)", rest)
        if not newline:
            return None
        end += newline.end()
    return reference_key(match[1]), end


def link_end(text, pos, label, references):
    if text.startswith("(", pos):
        start = pos + 1
        while start < len(text) and text[start].isspace():
            start += 1
        end = destination(text, start)
        if end is not None and allowed_destination(text[start:end]):
            tail = re.compile(r"(?:\s+" + TITLE + r")?\s*\)").match(text, end)
            if tail:
                return tail.end()
    if text.startswith("[]", pos):
        pos += 2
    elif text.startswith("[", pos):
        ref = re.compile(LABEL).match(text, pos)
        if ref:
            pos, label = ref.end(), ref[1]
    return pos if reference_key(label) in references else None


def closing_backticks(text, marker, pos):
    return re.compile(r"(?<!`)`{" + str(len(marker)) + r"}(?!`)").search(text, pos)


def punctuation(char):
    return char in string.punctuation or unicodedata.category(char).startswith(("P", "S"))


def delimiter_sides(text, start, end):
    left = text[start-1] if start else " "
    right = text[end] if end < len(text) else " "
    opens = not right.isspace() and (not punctuation(right) or left.isspace() or punctuation(left))
    closes = not left.isspace() and (not punctuation(left) or right.isspace() or punctuation(right))
    if text[start] == "_":
        return opens and (not closes or punctuation(left)), closes and (not opens or punctuation(right))
    return opens, closes


@dataclass
class Delimiter:
    value: str
    opens: bool
    closes: bool


def join_inline(pieces):
    """リンクの内側と外側で、それぞれ対応する強調記号だけを取り除く。"""
    output = []
    delimiters = {"*": [], "_": []}
    for piece in pieces:
        if isinstance(piece, str):
            output.append(piece)
            continue
        remaining = len(piece.value)
        stack = delimiters[piece.value[0]]
        while piece.closes and remaining and stack:
            index, both = stack[-1]
            size = len(output[index])
            if (piece.opens or both) and (size + remaining) % 3 == 0 and (size % 3 or remaining % 3):
                break
            used = min(size, remaining)
            output[index] = output[index][used:]
            remaining -= used
            if used == size:
                stack.pop()
        if piece.opens and remaining:
            stack.append((len(output), piece.closes))
        output.append(piece.value[:remaining])
    return "".join(output)


def inline(text, references):
    """コードを保護し、リンクの表示文と装飾を取り出してHTMLに渡す。"""
    pieces, brackets = [], []
    last_link = -1
    pos = 0
    while match := TOKEN.search(text, pos):
        pieces.append(escape(text[pos:match.start()]))
        value = match[0]
        pos = match.end()
        kind = match.lastgroup
        if kind == "code":
            close = closing_backticks(text, value, pos)
            if close:
                code = text[pos:close.start()].replace("\n", " ")
                if code.startswith(" ") and code.endswith(" ") and code.strip(" "):
                    code = code[1:-1]
                value = "<code>" + escape(code) + "</code>"
                pos = close.end()
        elif kind == "bracket":
            if value != "]":
                brackets.append((pos, len(pieces), last_link))
            elif brackets:
                start, index, previous_link = brackets.pop()
                image = pieces[index] == "!["
                if image or last_link < start:
                    end = link_end(text, pos, text[start:match.start()], references)
                    if end is not None:
                        value = "" if image else "<a>" + join_inline(pieces[index+1:]) + "</a>"
                        del pieces[index:]
                        last_link = previous_link if image else start
                        pos = end
        elif kind == "emphasis":
            value = Delimiter(value, *delimiter_sides(text, match.start(), pos))
        elif kind == "breakline":
            value = "<br>\n"
        elif kind == "auto":
            value = "<a>" + escape(value[1:-1]) + "</a>" if allowed_destination(value[1:-1]) else escape(value)
        elif kind == "escaped":
            value = escape(value[1:])
        elif kind == "entity":
            value = escape(unescape(value))
        pieces.append(value)
    pieces.append(escape(text[pos:]))
    return join_inline(pieces)


FRONT = re.compile(r"\A---[ \t]*\n.*?^(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S | re.M)
FENCE = re.compile(r" {0,3}(?:(`{3,})[^`]*|(~{3,}).*)$")
HEADING = re.compile(r" {0,3}#{1,6}(?:\s|$)")
SETEXT = re.compile(r" {0,3}(?:=+|-+)\s*$")
RULE = re.compile(r" {0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$")
QUOTE = re.compile(r" {0,3}> ?")
ITEM = re.compile(r" {0,3}(?:[-*+]|[0-9]{1,9}[.)])(?= |$)")
HTML_BLOCK = re.compile(
    r" {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|"
    r"dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|"
    r"head|header|hr|html|iframe|legend|li|link|main|menu|nav|noframes|ol|optgroup|option|p|param|"
    r"section|search|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?=[\s/>]|$)", re.I)
RAW_END = {"<!--": "-->", "<?": "?>", "<![CDATA[": "]]>", "<!": ">"}


def raw_end(line):
    """空行を含めて読むHTMLブロックは、専用の終了記号を持つ。"""
    start = line.lstrip(" ")
    match = re.match(r"<(script|pre|style|textarea)(?=[\s>]|$)", start, re.I)
    if match:
        return "</" + match[1].lower() + ">"
    return next((end for prefix, end in RAW_END.items() if start.startswith(prefix)), None)


def interrupts(line, in_container=False):
    """段落に割り込んで新しいブロックを開始できる行か。"""
    if not line.strip():
        return True
    if line.startswith("    "):
        return False
    marker = ITEM.match(line)
    if marker:
        return in_container or bool(line[marker.end():].strip()) and (
            not marker[0].strip()[0].isdigit() or marker[0].strip()[:-1] == "1")
    return bool(QUOTE.match(line) or HEADING.match(line) or FENCE.match(line)
                or RULE.fullmatch(line) or HTML_BLOCK.match(line) or raw_end(line))


def cells(line):
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):
        text = text[:-1]
    return [s.replace(r"\|", "|").strip() for s in re.split(r"(?<!\\)\|", text)]


def table_header(line, delimiter):
    if delimiter is None or "|" not in line or not re.fullmatch(r" {0,3}[:|-][:| -]+", delimiter):
        return None
    if delimiter.lstrip().startswith("- "):
        return None
    row, rules = cells(line), cells(delimiter)
    if len(row) == len(rules) and all(re.fullmatch(r":?-+:?", cell) for cell in rules):
        return row
    return None


@dataclass
class InlineText:
    value: str
    tag: str
    line: int

    def render(self, references):
        body = inline(self.value, references)
        if self.tag:
            return f'<{self.tag} {SOURCE_ATTR}="{self.line}">{body}</{self.tag}>'
        return body


@dataclass
class ListLayout:
    paragraphs: list = field(default_factory=list)
    loose: bool = False


class MarkdownBlocks:
    def __init__(self):
        self.parts = []
        self.references = set()

    def text_element(self, tag, line, text):
        element = InlineText(text, tag, line)
        self.parts.append(element)
        return element

    def reference_lines(self, read, first):
        lines = []
        index = first
        while (part := read(index, lazy=True)) is not None and part.strip():
            if index > first and interrupts(part):
                break
            lines.append(part.lstrip(" "))
            index += 1
        text = "\n".join(lines)
        consumed = 0
        while ref := definition(text):
            label, end = ref
            self.references.add(label)
            consumed += len(text[:end].splitlines())
            text = text[end:]
        return consumed

    def table_row(self, tag, line, row):
        self.parts.append("<tr>")
        for cell in row:
            self.text_element(tag, line, cell)
        self.parts.append("</tr>")

    def list_items(self, read, first, marker, depth):
        layout = ListLayout()
        bullet = marker[0][-1]
        self.parts.append("<ul>")
        while True:
            after = read(first)[marker.end():]
            spaces = len(after) - len(after.lstrip(" "))
            offset = marker.end() + (spaces if 1 <= spaces <= 4 else 1)

            def item(index, lazy=False):
                part = read(index, lazy)
                if part is None:
                    return None
                if index == first or part.startswith(" " * offset):
                    return part[offset:]
                if not part.strip():
                    return ""
                if lazy and not interrupts(part, in_container=True):
                    return part
                return None

            self.parts.append(f'<li {SOURCE_ATTR}="{first + 1}">')
            pos = self.blocks(item, first, depth + 1, layout)
            self.parts.append("</li>")
            following = read(pos) or ""
            marker = ITEM.match(following)
            if not marker or marker[0][-1] != bullet or RULE.fullmatch(following):
                break
            previous = read(pos - 1, lazy=True)
            if previous is not None and not previous.strip():
                layout.loose = True
            first = pos
        self.parts.append("</ul>")
        # 空行で区切ったブロック・項目があるときだけ、項目内の段落を改行する。
        if not layout.loose:
            for paragraph in layout.paragraphs:
                paragraph.tag = ""
        return pos

    def blocks(self, read, start=0, depth=0, layout=None):
        """readは現在の引用・リストに属する行を返す。段落の継続時だけ記号省略を許す。"""
        pos = start
        has_block = False
        while (line := read(pos)) is not None:
            first = pos
            pos += 1
            if not line.strip():
                continue
            if layout and has_block:
                previous = read(first - 1, lazy=True)
                if previous is not None and not previous.strip():
                    layout.loose = True
            has_block = True
            if line.startswith("    ") or HEADING.match(line) or RULE.fullmatch(line):
                continue
            if opener := FENCE.fullmatch(line):
                marker = opener[1] or opener[2]
                closing = re.compile(r" {0,3}" + marker[0] + "{" + str(len(marker)) + r",}\s*$")
                while (part := read(pos)) is not None:
                    pos += 1
                    if closing.fullmatch(part):
                        break
                continue

            if QUOTE.match(line):
                def quoted(index, lazy=False):
                    part = read(index, lazy)
                    if part is not None:
                        match = QUOTE.match(part)
                        if match:
                            return part[match.end():]
                        if lazy and not interrupts(part, in_container=True):
                            return part
                    return None

                if depth < 32:
                    self.parts.append("<blockquote>")
                    pos = self.blocks(quoted, first, depth + 1)
                    self.parts.append("</blockquote>")
                continue

            if marker := ITEM.match(line):
                if depth < 32:
                    pos = self.list_items(read, first, marker, depth)
                continue

            end = raw_end(line)
            if end or HTML_BLOCK.match(line) or re.fullmatch(r" {0,3}" + TAG + r"\s*", line):
                raw = [line]
                while (part := read(pos)) is not None:
                    if end and end.lower() in raw[-1].lower() or not end and not part.strip():
                        break
                    raw.append(part)
                    pos += 1
                self.parts.append(f'<div {SOURCE_ATTR}="{first + 1}">' + "\n".join(raw) + "</div>")
                continue

            header = table_header(line, read(pos))
            if header:
                self.parts.append("<table>")
                self.table_row("th", first + 1, header)
                pos += 1
                while (part := read(pos)) is not None and not interrupts(part, in_container=True) and not part.startswith("    "):
                    row = cells(part)
                    row.extend([""] * (len(header) - len(row)))
                    self.table_row("td", pos + 1, row[:len(header)])
                    pos += 1
                self.parts.append("</table>")
                continue

            if line.lstrip(" ").startswith("["):
                consumed = self.reference_lines(read, first)
                if consumed:
                    pos = first + consumed
                    continue

            paragraph = [line]
            heading = False
            while (part := read(pos, lazy=True)) is not None:
                if SETEXT.fullmatch(part) and read(pos) is not None:
                    pos += 1
                    heading = True
                    break
                if "|" in part and read(pos) is not None and table_header(part, read(pos + 1)):
                    break
                if interrupts(part):
                    break
                paragraph.append(part)
                pos += 1
            if not heading:
                text = "\n".join(part.lstrip(" ") for part in paragraph).strip()
                paragraph = self.text_element("p", first + 1, text)
                if layout:
                    layout.paragraphs.append(paragraph)
        return pos

    def render(self):
        # 文末の参照定義も解決できるよう、本文の展開はブロックの確定後に行う。
        return "\n".join(part.render(self.references) if isinstance(part, InlineText) else part
                         for part in self.parts)


def extract_units(text):
    """原文行順に line, kind, display, masked, link_only を持つ検査単位を返す。"""
    front = FRONT.match(text)
    if front:
        text = "\n" * front[0].count("\n") + text[front.end():]
    lines = text.expandtabs(4).split("\n")
    blocks = MarkdownBlocks()
    blocks.blocks(lambda index, lazy=False: lines[index] if index < len(lines) else None)
    return html_units(blocks.render())
