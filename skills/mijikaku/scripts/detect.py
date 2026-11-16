#!/usr/bin/env python3
"""Markdownの見直し候補・理由・原文の行番号をJSONへ出力する。"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
SOURCE_ATTR = "data-doccheck-line"
SKIP = {"pre", "script", "style", "template", "noscript", "svg", "math", "img", "blockquote"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
NOTE_PAIRS = {"（": "）", "(": ")", "【": "】", "［": "］"}
PAIRS = NOTE_PAIRS | {"「": "」", "『": "』"}
QUOTED = re.compile(r"""「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'""")

# ID、分類、正規表現、検出理由。
REGEX_RULES = [
    ("否定-文末", "否定・対比",
     r"(?s)^(?:.*(?:ない|なかった|無い|無かった|ません(?:でした)?|なし|無し|(?<![はま])ず)(?:です|でした|とする|こと(?:とする)?|ものとする|でください)?|.+(?:不要|対象外|未使用)(?:です|である|とする|となる)?)[。．.!?！？ \t]*$",
     "文末の否定形"),
    ("否定-対比", "否定・対比",
     r"(?<!だけ)(?<!のみ)(?:ではなく(?:て)?|(?<!空)でなく(?:て)?|では(?:ないが|ありませんが|ないものの))[、，,]?[ \t]*",
     "否定を伴う対比"),
    ("前置き-補足", "前置き・予告",
     r"^(?:(?:なお|ちなみに)[、，,][ \t]*|(?:補足すると|付け加えると|参考までに|念のため)[、，,]?[ \t]*|(?:※[ \t]*|(?:補足|備考|注意|注)[：:][ \t]*)(?=\S))",
     "補足の前置き"),
    ("前置き-言い換え", "前置き・予告",
     r"^(?:つまり|すなわち|言い換えると|換言すると)[、，,][ \t]*",
     "言い換えの前置き"),
    ("前置き-強調", "前置き・予告",
     r"^(?:ここで(?:重要|大切|注意すべき)なのは[、，,]?[ \t]*|(?:重要|大切)な点として[、，,][ \t]*)",
     "強調の前置き"),
    ("前置き-予告", "前置き・予告",
     r"(?:以下|次)の(?:とおり|通り)(?:です|である|とする)?[。．.!?！？：:」』）)】］ \t]*$",
     "説明の予告"),
    ("言い回し-可能表現", "長い言い回し",
     r"すること(?:が(?:可能です|可能である|可能だ|できます|できる)|は(?:できません|できない|不可能です))",
     "長い可能表現"),
    ("言い回し-接続表現", "長い言い回し",
     r"については|に関しては|において(?:は)?",
     "長い接続表現"),
    ("重複-語句", "繰り返し",
     r"あらかじめ事前に|予め事前に|事前にあらかじめ|まず最初に|一番最初に|最も最適",
     "語の重なり"),
]
# 単位ごとの表示名、文字数上限、文数上限。
LIMITS = {"paragraph": ("段落", 240, 3), "table_cell": ("表セル", 120, 2),
          "list_item": ("箇条書き", 160, 2)}
MIN_SENTENCE_CHARACTERS = 60


def decode_source(data):
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


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


def sentences(text):
    """引用・括弧内を除く。！？!?で区切る。改行では切らない。"""
    stack, start = [], 0
    for i, char in enumerate(text):
        if char in PAIRS:
            stack.append(char)
        elif stack and char == PAIRS[stack[-1]]:
            stack.pop()
        if char in "。！？!?" and not stack:
            if any(c.isalnum() or c == "\ufffc" for c in text[start:i + 1]):
                yield start, i + 1
            start = i + 1
    if any(c.isalnum() or c == "\ufffc" for c in text[start:]):
        yield start, len(text)


def notes(text):
    stack = []
    for i, char in enumerate(text):
        if char in NOTE_PAIRS:
            stack.append((char, i))
        elif char in NOTE_PAIRS.values():
            if stack and char == NOTE_PAIRS[stack[-1][0]]:
                _, start = stack.pop()
                yield start, i + 1
            else:
                stack.clear()


def scan_text(text, relative_path):
    findings, seen = [], {}
    for unit in extract_units(text):
        visible, display = unit["masked"], unit["display"]
        bounds = list(sentences(visible))
        reasons = []

        def add(rule_id, group, reason, matches=(), **extra):
            if matches:
                extra["matches"] = list(dict.fromkeys(matches))
            reasons.append({"rule_id": rule_id, "group": group, "reason": reason, **extra})

        for rule_id, group, pattern, reason in REGEX_RULES:
            searchable = QUOTED.sub(lambda m: " " * len(m[0]), visible) if rule_id == "否定-文末" else visible
            matches = []
            for a, b in bounds:
                segment = searchable[a:b]
                offset = a + len(segment) - len(segment.lstrip())
                matches.extend(display[offset + m.start():offset + m.end()]
                               for m in re.finditer(pattern, segment.strip()))
            if matches:
                add(rule_id, group, reason, matches)
        matches = [display[a:b] for a, b in notes(visible)]
        if matches:
            add("括弧-補足", "括弧書き", "括弧書き", matches)
        matches, previous = [], []
        if not unit["link_only"]:
            for a, b in bounds:
                sentence = display[a:b].strip()
                if len(sentence) < 20 or sentence[-1] not in "。！？!?":
                    continue
                if sentence in seen:
                    matches.append(sentence)
                    previous.append(seen[sentence])
                else:
                    seen[sentence] = unit["line"]
        if matches:
            add("重複-文", "繰り返し", "同文の繰り返し",
                matches, first_seen_lines=sorted(set(previous)))
        name, max_chars, max_sentences = LIMITS[unit["kind"]]
        chars, count = sum(not c.isspace() for c in display), len(bounds)
        if chars > max_chars or (chars >= MIN_SENTENCE_CHARACTERS and count > max_sentences):
            noun = "箇条書き項目" if unit["kind"] == "list_item" else name
            add("長さ-" + name, "文字数・文数",
                "長い" + noun,
                metrics={"characters": chars, "sentences": count, "max_characters": max_chars,
                         "max_sentences": max_sentences, "min_characters_for_sentences": MIN_SENTENCE_CHARACTERS})
        if reasons:
            findings.append({"file": relative_path, "line": unit["line"], "unit_type": unit["kind"],
                             "display": display, "reasons": reasons})
    return findings


def scan(source):
    source = Path(source).resolve()
    if source.is_file() and source.suffix == ".md":
        paths, base = [source], source.parent
    elif source.is_dir():
        paths, base = sorted(source.rglob("*.md")), source
        if any(p.is_symlink() for p in source.rglob("*")):
            raise ValueError("入力内のシンボリックリンクは扱いません")
    else:
        raise ValueError("入力には .md ファイルまたはディレクトリを指定してください")
    if not paths:
        raise ValueError("入力ディレクトリに .md ファイルがありません")
    findings = []
    for path in paths:
        findings.extend(scan_text(decode_source(path.read_bytes()), path.relative_to(base).as_posix()))
    manifest = HERE.parents[2] / "plugin.json"
    version = json.loads(manifest.read_text(encoding="utf-8")).get("version") if manifest.is_file() else None
    return {"plugin_version": version, "source": str(source), "findings": findings}


def output_candidates(report):
    candidates = {}
    for finding in report["findings"]:
        reasons = []
        for item in finding["reasons"]:
            reason = item["reason"]
            if "metrics" in item:
                metrics = item["metrics"]
                reason += f"：{metrics['characters']}字・{metrics['sentences']}文"
            if "first_seen_lines" in item:
                reason += "：先行" + "・".join(map(str, item["first_seen_lines"])) + "行"
            reasons.append(reason)
        candidates.setdefault(finding["file"], []).append(
            {"line": finding["line"], "text": finding["display"], "reason": "／".join(reasons)})
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help=".md ファイルまたはディレクトリ")
    parser.add_argument("--out", type=Path, required=True, help="新しいJSONファイルの出力先")
    args = parser.parse_args()
    try:
        source, out = args.source.resolve(), args.out.resolve()
        if out == source or (source.is_dir() and source in out.parents):
            raise ValueError("出力先は検査対象の外へ置いてください")
        report = scan(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("x", encoding="utf-8") as stream:
            json.dump(output_candidates(report), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(f"{len(report['findings'])}箇所: {out}")
    except (OSError, ValueError) as error:
        parser.exit(2, f"検査できませんでした: {error}\n")


if __name__ == "__main__":
    main()
