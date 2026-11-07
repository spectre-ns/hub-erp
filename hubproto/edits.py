"""Targeted edits to documents: the operations a cog (or anyone) can ask for, and how each is applied to the file.

A cog answers an edit request with a short list of operations instead of a whole new text, so it can delete,
replace and insert at a place, not only add at the start or the end:

    @@ insert at start | @@ insert at end | @@ insert after: <text> | @@ insert before: <text>
    @@ append to section: <heading>
    @@ replace: <exact text>  /  @@ with: <new text>        (inline, every occurrence, keeps formatting)
    @@ replace paragraph: <text>    @@ replace section: <heading>    @@ rewrite
    @@ delete: <exact text>   @@ delete paragraph: <text>   @@ delete section: <heading>

and for spreadsheets:

    @@ append rows | @@ insert rows at: <n> | @@ set: B3: <value> | @@ clear: B2:D4 | @@ delete rows: 4-6
    @@ replace: <text> / @@ with: <text>

Lines after an operation (until the next '@@') are its content, in the writing markup (headings, bullets, tables,
'| a | b |' rows). ``apply_ops`` applies them to a docx, odt, md/txt, xlsx or ods file and reports each one;
``op_applied`` tells from the text after a live edit whether an operation took effect, so the hub can finish in
the file whatever the live editor did not."""

from __future__ import annotations

import html
import io
import re
import zipfile

from .office import _col, _escape, _zip, docx_blocks, extract_text, insert_rows, is_sheet, rows_from_lines

TEXT_OPS = ("insert", "replace", "replace_paragraph", "replace_section", "delete", "delete_paragraph", "delete_section", "rewrite")
SHEET_OPS = ("append_rows", "insert_rows", "set", "clear", "delete_rows", "replace")

EDIT_INTERFACE = {
    "text": """OUTPUT FORMAT for a change to this document: a list of OPERATIONS. Each starts with a line beginning '@@'; the lines
after it (until the next '@@') are its content, written in the markup below.
@@ insert at start                       then the lines to add at the top
@@ insert at end                         then the lines to add at the bottom
@@ insert after: <text of a paragraph or heading>     then the lines to add right after it
@@ insert before: <text of a paragraph or heading>    then the lines to add right before it
@@ append to section: <heading>          then the lines to add at the end of that section
@@ replace: <exact text as it is in the document>
@@ with: <new text>                      (replaces every occurrence inside its paragraph; keeps the formatting)
@@ replace paragraph: <text from the paragraph>       then the new line(s) for it
@@ replace section: <heading>            then the new body of the section (the heading stays)
@@ delete: <exact text>                  (removes it; a whole paragraph's text removes the paragraph)
@@ delete paragraph: <text from the paragraph>
@@ delete section: <heading>             (the heading and everything under it, up to the next heading of its level)
@@ rewrite                               then the WHOLE document (only when asked to rewrite all of it)
Quote text EXACTLY as it appears in the document above (copy it, short but unique). Use the fewest operations that do
what was asked; never rewrite to make a small change. No explanation, no code fences.
MARKUP for content lines: '# ', '## ', '### ' headings; a plain line is a paragraph; '- item' bullets; '1. item' numbers;
'**bold**', '*italic*'; a table is rows '| a | b |' with '|---|---|' after the header row; '---' alone is a page break.""",
    "sheet": """OUTPUT FORMAT for a change to this spreadsheet: a list of OPERATIONS, each on a line beginning '@@'; rows follow
the operations that take them, one per line as '| a | b | c |'.
@@ append rows                then the rows to add after the last used row
@@ insert rows at: <n>        then the rows to insert before row n (rows below move down)
@@ set: <cell>: <value>       e.g. '@@ set: B3: 42' or '@@ set: D2: =SUM(D3:D9)'
@@ clear: <cell or range>     e.g. '@@ clear: B2:D4'
@@ delete rows: <n> or <n>-<m>   (rows below move up)
@@ replace: <exact text in a cell>
@@ with: <new text>
Cells are addressed like B3 (column letter, row number) as in the grid above. A number is stored as a number; a
value starting with '=' is a formula. Formulas elsewhere are not adjusted when rows move. No explanation, no code fences.""",
}


# ---------------------------------------------------------------- parsing what the model wrote
_HEAD = [
    (r"insert (?:at )?(?:the )?(start|beginning|top)(?: of the (?:document|sheet))?", lambda m: {"op": "insert", "at": "start"}),
    (r"insert (?:at )?(?:the )?(end|bottom)(?: of the (?:document|sheet))?", lambda m: {"op": "insert", "at": "end"}),
    (r"insert after:\s*(.+)", lambda m: {"op": "insert", "after": m.group(1)}),
    (r"insert before:\s*(.+)", lambda m: {"op": "insert", "before": m.group(1)}),
    (r"append to section:\s*(.+)", lambda m: {"op": "insert", "section_end": m.group(1)}),
    (r"replace paragraph:\s*(.+)", lambda m: {"op": "replace_paragraph", "target": m.group(1)}),
    (r"replace section:\s*(.+)", lambda m: {"op": "replace_section", "target": m.group(1)}),
    (r"replace:\s*(.+)", lambda m: {"op": "replace", "target": m.group(1), "with": None}),
    (r"delete paragraph:\s*(.+)", lambda m: {"op": "delete_paragraph", "target": m.group(1)}),
    (r"delete section:\s*(.+)", lambda m: {"op": "delete_section", "target": m.group(1)}),
    (r"delete rows?:\s*(\d+)(?:\s*(?:-|–|to)\s*(\d+))?", lambda m: {"op": "delete_rows", "from": int(m.group(1)), "to": int(m.group(2) or m.group(1))}),
    (r"delete:\s*(.+)", lambda m: {"op": "delete", "target": m.group(1)}),
    (r"rewrite(?: the (?:whole )?document)?", lambda m: {"op": "rewrite"}),
    (r"append rows?", lambda m: {"op": "append_rows"}),
    (r"insert rows? (?:at|before)(?: row)?:?\s*(\d+)", lambda m: {"op": "insert_rows", "at": int(m.group(1))}),
    (r"set:?\s*([A-Za-z]{1,3}\d+)\s*[:=]\s*(.*)", lambda m: {"op": "set", "cell": m.group(1).upper(), "value": m.group(2)}),
    (r"clear:?\s*([A-Za-z]{1,3}\d+(?:\s*:\s*[A-Za-z]{1,3}\d+)?)", lambda m: {"op": "clear", "range": re.sub(r"\s", "", m.group(1)).upper()}),
]
TAKES_LINES = {"insert", "replace_paragraph", "replace_section", "rewrite", "append_rows", "insert_rows"}


def _unquote(s: str) -> str:
    s = s.strip()
    for a, b in (('"', '"'), ("'", "'"), ("`", "`"), ("“", "”"), ("‘", "’"), ("«", "»")):
        if len(s) >= 2 and s.startswith(a) and s.endswith(b):
            return s[1:-1]
    return s


def parse_ops(text: str) -> list[dict]:
    """The operations in a model's answer. Lines before any '@@' are ignored unless there is no operation at all,
    in which case they are returned as one 'insert at start' (an older answer style)."""
    ops: list[dict] = []
    loose: list[str] = []
    cur: dict | None = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            continue
        m = re.match(r"^\s*@@\s*(.*?)\s*(?:@@)?\s*$", line)
        if m:
            head = m.group(1).strip()
            w = re.match(r"(?i)^with:?\s*(.*)$", head)
            if w and ops and ops[-1]["op"] == "replace" and ops[-1].get("with") is None:
                ops[-1]["with"] = _unquote(w.group(1))
                cur = ops[-1]
                continue
            for pat, make in _HEAD:
                hm = re.fullmatch(r"(?i)" + pat, head)
                if hm:
                    op = make(hm)
                    for k in ("target", "after", "before", "section_end"):
                        if isinstance(op.get(k), str):
                            op[k] = _unquote(op[k])
                    if op["op"] == "set":
                        op["value"] = _unquote(op["value"])
                    if op["op"] in TAKES_LINES:
                        op["lines"] = []
                    ops.append(op)
                    cur = op
                    break
            else:
                ops.append({"op": "unknown", "text": head})
                cur = None
            continue
        if cur is not None and cur["op"] in TAKES_LINES:
            cur["lines"].append(line)
        elif cur is not None and cur["op"] == "replace" and cur.get("with") is None and line.strip():
            cur["with"] = _unquote(line.strip())  # the new text on the line after '@@ replace:'
        elif cur is None:
            loose.append(line)
    for op in ops:
        if "lines" in op:
            while op["lines"] and not op["lines"][-1].strip():
                op["lines"].pop()
            while op["lines"] and not op["lines"][0].strip():
                op["lines"].pop(0)
    if not ops and any(x.strip() for x in loose):
        ops = [{"op": "insert", "at": "start", "lines": [x for x in loose if x.strip()]}]
    return ops


def describe(op: dict) -> str:
    o = op["op"]
    n = len(op.get("lines") or [])
    if o == "insert":
        where = ("at the start" if op.get("at") == "start" else "at the end" if op.get("at") == "end" else
                 f"after “{_short(op['after'])}”" if op.get("after") else f"before “{_short(op['before'])}”" if op.get("before") else
                 f"at the end of section “{_short(op['section_end'])}”")
        return f"insert {n} line(s) {where}"
    if o == "replace":
        return f"replace “{_short(op['target'])}” with “{_short(op.get('with') or '')}”" if op.get("with") else f"remove “{_short(op['target'])}”"
    if o in ("delete", "delete_paragraph", "delete_section", "replace_paragraph", "replace_section"):
        return f"{o.replace('_', ' ')} “{_short(op['target'])}”" + (f" ({n} line(s))" if n else "")
    if o == "rewrite":
        return f"rewrite the whole document ({n} line(s))"
    if o == "append_rows":
        return f"append {n} row(s)"
    if o == "insert_rows":
        return f"insert {n} row(s) at row {op['at']}"
    if o == "set":
        return f"set {op['cell']} to “{_short(op['value'])}”"
    if o == "clear":
        return f"clear {op['range']}"
    if o == "delete_rows":
        return f"delete row {op['from']}" if op["from"] == op["to"] else f"delete rows {op['from']}-{op['to']}"
    return f"“{_short(op.get('text') or o)}” (not an operation I know)"


def _short(s: str, n: int = 60) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- matching text
_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " ", "–": "-", "—": "-", " ": " "})


def fold(s: str) -> str:
    """Same length as s: curly quotes, dashes and non-breaking spaces made plain, lower case."""
    return (s or "").translate(_FOLD).lower()


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", fold(s)).strip()


def plain_line(line: str) -> str:
    """A content line as the text it becomes (markup removed)."""
    t = line.strip()
    if re.fullmatch(r"\|[\s:|-]+\|", t) or t == "---":
        return ""
    if t.startswith("|") and t.endswith("|"):
        t = " ".join(c.strip() for c in t.strip("|").split("|"))
    t = re.sub(r"^(#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)", "", t)
    return t.replace("**", "").replace("*", "").strip()


def _find(full: str, target: str, start: int = 0) -> tuple[int, int] | None:
    """Exact first, then case/quote-folded (both keep offsets)."""
    if not target:
        return None
    i = full.find(target, start)
    if i >= 0:
        return i, i + len(target)
    i = fold(full).find(fold(target), start)
    if i >= 0:
        return i, i + len(target)
    return None


# ---------------------------------------------------------------- the block model of a text document
class Block:
    __slots__ = ("raw", "kind", "level", "text")

    def __init__(self, raw: str, kind: str, level: int = 0, text: str = ""):
        self.raw, self.kind, self.level, self.text = raw, kind, level, text


_TAG = re.compile(r"<(/?)([A-Za-z_][\w.\-]*:[\w.\-]+|[A-Za-z_][\w.\-]*)\b[^>]*?(/?)>", re.S)


def _top_spans(xml: str) -> list[tuple[int, int, str]]:
    """(start, end, tag name) of each top-level element in an XML fragment; text between elements is dropped."""
    out, depth, start, name = [], 0, 0, ""
    for m in _TAG.finditer(xml):
        closing, tag, selfclose = m.group(1) == "/", m.group(2), m.group(3) == "/"
        if m.group(0).startswith("<?") or m.group(0).startswith("<!"):
            continue
        if not closing and depth == 0:
            start, name = m.start(), tag
        if closing:
            depth -= 1
            if depth == 0:
                out.append((start, m.end(), name))
        elif selfclose:
            if depth == 0:
                out.append((start, m.end(), name))
        else:
            depth += 1
    return out


# docx --------------------------------------------------------------
_DOCX_MARKERS = re.compile(r"<w:commentRangeStart\b[^>]*/>|<w:commentRangeEnd\b[^>]*/>|<w:r>(?:<w:rPr>.*?</w:rPr>)?<w:commentReference\b[^>]*/></w:r>"
                           r"|<w:r\b[^>]*>(?:(?!</w:r>).)*?<w:commentReference\b[^>]*/>(?:(?!</w:r>).)*?</w:r>|<w:bookmarkStart\b[^>]*/>|<w:bookmarkEnd\b[^>]*/>", re.S)


def _docx_text(raw: str) -> str:
    return html.unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", raw, re.S)))


def _docx_block(raw: str, name: str) -> Block:
    if name == "w:p":
        m = re.search(r'<w:pStyle w:val="([^"]+)"', raw)
        style = (m.group(1) if m else "").lower()
        hm = re.match(r"(?:heading|berschrift|titre)\s*(\d)", style.replace("ü", ""))
        level = int(hm.group(1)) if hm else (1 if style == "title" else 0)
        lvl = re.search(r'<w:outlineLvl w:val="(\d)"', raw)
        if not level and lvl:
            level = int(lvl.group(1)) + 1
        return Block(raw, "h" if level else "p", level, _docx_text(raw))
    if name == "w:tbl":
        rows = re.findall(r"<w:tr\b.*?</w:tr>", raw, re.S)
        return Block(raw, "tbl", 0, "\n".join(" | ".join(_docx_text(c) for c in re.findall(r"<w:tc\b.*?</w:tc>", r, re.S)) for r in rows))
    if name == "w:sectPr":
        return Block(raw, "sect")
    return Block(raw, "other", 0, _docx_text(raw))


def _odt_mask(xml: str) -> tuple[str, list[str]]:
    """Comments (office:annotation) hold their own paragraphs: masked as <hubann/> tags while we work."""
    saved: list[str] = []

    def keep(m):
        saved.append(m.group(0))
        return f'<hubann n="{len(saved) - 1}"/>'
    return re.sub(r"<office:annotation\b(?:(?!/>)[^>])*>.*?</office:annotation>", keep, xml, flags=re.S), saved


def _odt_unmask(xml: str, saved: list[str]) -> str:
    return re.sub(r'<hubann n="(\d+)"/>', lambda m: saved[int(m.group(1))], xml)


def _odt_text(raw: str) -> str:
    raw = re.sub(r"<hubann[^>]*/>", "", raw)
    raw = re.sub(r"<text:s(?:\s+text:c=\"(\d+)\")?\s*/>", lambda m: " " * int(m.group(1) or 1), raw)
    raw = re.sub(r"<text:tab\s*/>", "\t", raw)
    raw = re.sub(r"</text:p>|</text:h>", "\n", raw)
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _odt_block(raw: str, name: str) -> Block:
    if name == "text:h":
        m = re.search(r'text:outline-level="(\d)"', raw)
        return Block(raw, "h", int(m.group(1)) if m else 1, _odt_text(raw))
    if name == "text:p":
        return Block(raw, "p", 0, _odt_text(raw))
    if name == "text:list":
        return Block(raw, "p", 0, _odt_text(raw))
    if name == "table:table":
        rows = re.findall(r"<table:table-row\b.*?</table:table-row>", raw, re.S)
        return Block(raw, "tbl", 0, "\n".join(" | ".join(_odt_text(c) for c in re.findall(r"<table:table-cell\b.*?</table:table-cell>", r, re.S)) for r in rows))
    if name in ("text:sequence-decls", "office:forms", "text:variable-decls", "text:user-field-decls"):
        return Block(raw, "sect")  # declarations at the top: kept where they are
    return Block(raw, "other", 0, _odt_text(raw))


def odt_blocks(lines: list[str]) -> list[str]:
    """Paragraphs, headings and tables in ODF for lines in the writing markup."""
    out: list[str] = []
    table: list[list[str]] = []

    def flush():
        if not table:
            return
        cols = max(len(r) for r in table)
        rows = "".join("<table:table-row>" + "".join(f'<table:table-cell office:value-type="string"><text:p>{_escape(c.strip().replace("**", ""))}</text:p></table:table-cell>'
                                                     for c in r + [""] * (cols - len(r))) + "</table:table-row>" for r in table)
        out.append(f'<table:table table:name="HubTable{len(out) + 1}"><table:table-column table:number-columns-repeated="{cols}"/>{rows}</table:table>')
        table.clear()
    for line in lines:
        t = line.strip()
        if t.startswith("|") and t.endswith("|"):
            if not re.fullmatch(r"\|[\s:|-]+\|", t):
                table.append(t.strip("|").split("|"))
            continue
        flush()
        if not t:
            continue
        hm = re.match(r"^(#{1,3})\s+(.*)$", t)
        if hm:
            out.append(f'<text:h text:outline-level="{len(hm.group(1))}">{_escape(hm.group(2).replace("**", ""))}</text:h>')
        elif t == "---":
            out.append("<text:p/>")
        elif re.match(r"^[-*•] ", t):
            out.append(f"<text:p>{_escape(chr(8226) + ' ' + t[2:].strip().replace('**', ''))}</text:p>")
        else:
            out.append(f"<text:p>{_escape(t.replace('**', ''))}</text:p>")
    flush()
    return out


class TextDoc:
    """A text document as a list of top-level blocks, for docx, odt and md/txt."""

    def __init__(self, content: bytes, ext: str):
        self.ext = ext
        self.files: dict[str, bytes] = {}
        self.names: list[str] = []
        self.saved: list[str] = []
        if ext in ("md", "txt"):
            text = content.decode("utf-8", "replace")
            self.blocks = [self._md_block(x) for x in text.split("\n")]
            return
        z = _zip(content)
        if z is None:
            raise ValueError("not a zip package")
        self.names = z.namelist()
        self.files = {n: z.read(n) for n in self.names}
        part = "word/document.xml" if ext == "docx" else "content.xml"
        if part not in self.files:
            raise ValueError(f"no {part} in the file")
        xml = self.files[part].decode("utf-8")
        if ext == "docx":
            xml = re.sub(r"<w:body\s*/>", "<w:body></w:body>", xml, count=1)
            m = re.search(r"(<w:body\b[^>]*>)(.*)(</w:body>)", xml, re.S)
        else:
            xml, self.saved = _odt_mask(xml)
            xml = re.sub(r"<office:text((?:\s[^>]*?)?)\s*/>", r"<office:text\1></office:text>", xml, count=1)
            m = re.search(r"(<office:text\b[^>]*>)(.*)(</office:text>)", xml, re.S)
        if not m:
            raise ValueError("no document body")
        self.head, self.tail = xml[: m.end(1)], xml[m.start(3):]
        inner = m.group(2)
        make = _docx_block if ext == "docx" else _odt_block
        self.blocks = [make(inner[a:b], name) for a, b, name in _top_spans(inner)]

    @staticmethod
    def _md_block(line: str) -> Block:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            return Block(line, "h", len(m.group(1)), m.group(2).strip())
        if not line.strip():
            return Block(line, "blank")
        return Block(line, "p", 0, plain_line(line))

    def new_blocks(self, lines: list[str]) -> list[Block]:
        if self.ext in ("md", "txt"):
            return [self._md_block(x) for x in lines]
        if self.ext == "docx":
            return [_docx_block(raw, "w:tbl" if raw.startswith("<w:tbl") else "w:p") for raw in docx_blocks(lines)]
        return [_odt_block(raw, re.match(r"<([\w:]+)", raw).group(1)) for raw in odt_blocks(lines)]

    # -- finding
    def content_range(self) -> tuple[int, int]:
        """Indexes of the editable blocks: after leading declarations, before the section properties."""
        lo = 0
        while lo < len(self.blocks) and self.blocks[lo].kind == "sect" and self.ext == "odt":
            lo += 1
        hi = len(self.blocks)
        while hi > lo and self.blocks[hi - 1].kind == "sect" and self.ext == "docx":
            hi -= 1
        return lo, hi

    def find_block(self, target: str, kinds=("p", "h", "tbl", "other")) -> int | None:
        want = squash(target)
        if not want:
            return None
        lo, hi = self.content_range()
        cands = [i for i in range(lo, hi) if self.blocks[i].kind in kinds]
        for test in (lambda t: t == want, lambda t: t.startswith(want), lambda t: want in t):
            for i in cands:
                if test(squash(self.blocks[i].text)):
                    return i
        return None

    def find_heading(self, target: str) -> int | None:
        i = self.find_block(target, kinds=("h",))
        if i is None:  # a heading written as a bold paragraph, or quoted with its number
            i = self.find_block(re.sub(r"^\s*\d+(\.\d+)*[.)]?\s+", "", target), kinds=("h",))
        return i

    def section_end(self, i: int) -> int:
        lvl = self.blocks[i].level or 1
        lo, hi = self.content_range()
        j = i + 1
        while j < hi and not (self.blocks[j].kind == "h" and self.blocks[j].level <= lvl):
            j += 1
        return j

    # -- changing
    def _markers(self, blocks: list[Block]) -> str:
        if self.ext == "docx":
            return "".join(m.group(0) for b in blocks for m in _DOCX_MARKERS.finditer(b.raw))
        if self.ext == "odt":
            return "".join(re.findall(r"<hubann[^>]*/>|<office:annotation-end\b[^>]*/>|<text:bookmark(?:-start|-end)?\b[^>]*/>", "".join(b.raw for b in blocks)))
        return ""

    def _attach(self, markers: str, near: int) -> None:
        """Comment anchors from removed blocks go into the nearest paragraph that stays, so no comment is orphaned."""
        if not markers or self.ext not in ("docx", "odt"):
            return
        lo, hi = self.content_range()
        order = [j for j in range(near - 1, lo - 1, -1)] + [j for j in range(near, hi)]
        for j in order:
            b = self.blocks[j]
            if b.kind not in ("p", "h"):
                continue
            if self.ext == "docx":
                b.raw = re.sub(r"<w:p((?:\s[^>]*)?)/>$", r"<w:p\1></w:p>", b.raw)
                b.raw = b.raw[: b.raw.rfind("</w:p>")] + markers + "</w:p>"
            else:
                b.raw = re.sub(r"<(text:[ph])((?:\s[^>]*)?)/>$", r"<\1\2></\1>", b.raw)
                end = max(b.raw.rfind("</text:p>"), b.raw.rfind("</text:h>"))
                b.raw = b.raw[:end] + markers + b.raw[end:]
            return
        self.blocks.insert(lo, Block(f"<w:p>{markers}</w:p>" if self.ext == "docx" else f"<text:p>{markers}</text:p>", "p"))

    def remove(self, a: int, b: int, keep_markers_near: int | None = None) -> None:
        gone = self.blocks[a:b]
        del self.blocks[a:b]
        self._attach(self._markers(gone), a if keep_markers_near is None else keep_markers_near)

    def insert(self, at: int, new: list[Block]) -> None:
        self.blocks[at:at] = new

    def inline_replace(self, target: str, new: str, *, whole_para_removes: bool = False) -> int:
        """Replace every occurrence of target inside paragraphs (tables too), keeping the runs' formatting."""
        if self.ext in ("md", "txt"):
            count = 0
            for b in self.blocks:
                if b.kind == "blank":
                    continue
                if whole_para_removes and squash(b.text) == squash(target):
                    b.raw, b.kind, b.text = "", "gone", ""
                    count += 1
                    continue
                pos = 0
                while True:
                    hit = _find(b.raw, target, pos)
                    if not hit:
                        break
                    b.raw = b.raw[: hit[0]] + new + b.raw[hit[1]:]
                    pos = hit[0] + len(new)
                    count += 1
                b.text = plain_line(b.raw)
            self.blocks = [b for b in self.blocks if b.kind != "gone"]
            return count
        count = 0
        lo, hi = self.content_range()
        keep: list[Block] = []
        for idx, b in enumerate(self.blocks):
            if idx < lo or idx >= hi or b.kind not in ("p", "h", "tbl", "other"):
                keep.append(b)
                continue
            if whole_para_removes and b.kind in ("p", "h") and squash(b.text) == squash(target):
                count += 1
                markers = self._markers([b])
                if markers:  # keep the comment anchors in an empty paragraph rather than lose the comment
                    keep.append(Block(f"<w:p>{markers}</w:p>" if self.ext == "docx" else f"<text:p>{markers}</text:p>", "p"))
                continue
            raw, n = _replace_in_paragraphs(b.raw, target, new, self.ext)
            if n:
                count += n
                b.raw = raw
                b.text = _docx_text(raw) if self.ext == "docx" else _odt_text(raw)
            keep.append(b)
        self.blocks = keep
        return count

    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text)

    def to_bytes(self) -> bytes:
        if self.ext in ("md", "txt"):
            out = "\n".join(b.raw for b in self.blocks)
            return (out.rstrip("\n") + "\n").encode("utf-8") if out.strip() else b""
        body = "".join(b.raw for b in self.blocks)
        if self.ext == "docx":
            lo, hi = self.content_range()
            if not any(b.kind in ("p", "tbl") for b in self.blocks[lo:hi]) and not any(b.kind == "h" for b in self.blocks):
                body = "".join(b.raw for b in self.blocks[:lo]) + "<w:p/>" + "".join(b.raw for b in self.blocks[lo:])
            self.files["word/document.xml"] = (self.head + body + self.tail).encode("utf-8")
        else:
            if not any(b.kind in ("p", "h", "tbl") for b in self.blocks):
                body += "<text:p/>"
            self.files["content.xml"] = _odt_unmask(self.head + body + self.tail, self.saved).encode("utf-8")
        return _rezip(self.names, self.files)


def _rezip(names: list[str], files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for n in names:
            if n == "mimetype":
                out.writestr(zipfile.ZipInfo(n), files[n], compress_type=zipfile.ZIP_STORED)
            else:
                out.writestr(n, files[n])
    return buf.getvalue()


def _segments(raw: str, ext: str) -> list[tuple[int, int, str]]:
    """The editable text pieces of a paragraph's XML: (start, end, unescaped text) of each, in order."""
    if ext == "docx":
        return [(m.start(1), m.end(1), html.unescape(m.group(1))) for m in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", raw, re.S)]
    out = []
    pos = 0
    for m in re.finditer(r"<[^>]+>", raw):
        if m.start() > pos:
            out.append((pos, m.start(), html.unescape(raw[pos:m.start()])))
        pos = m.end()
    if pos < len(raw):
        out.append((pos, len(raw), html.unescape(raw[pos:])))
    return out


def _replace_once(raw: str, segs: list[tuple[int, int, str]], s: int, e: int, new: str, ext: str) -> str:
    pieces = []
    offset = 0
    if s == e:  # a pure insertion at a point: into the run that ends (or holds) that point
        for a, b, t in segs:
            lo, hi = offset, offset + len(t)
            offset = hi
            if lo <= s <= hi:
                raw = raw[:a] + _escape(t[: s - lo] + new + t[s - lo:]) + raw[b:]
                break
        if ext == "docx":
            raw = re.sub(r"<w:t>|<w:t xml:space=\"preserve\">", '<w:t xml:space="preserve">', raw)
        return raw
    for a, b, t in segs:
        lo, hi = offset, offset + len(t)
        offset = hi
        if hi <= s or lo >= e:
            continue
        if lo <= s < hi or (s == lo and lo == hi):
            keep_tail = t[e - lo:] if e <= hi else ""
            text = t[: s - lo] + new + keep_tail
        else:
            text = t[e - lo:] if e < hi else ""
        pieces.append((a, b, text))
    for a, b, text in reversed(pieces):
        raw = raw[:a] + _escape(text) + raw[b:]
    if ext == "docx":
        raw = re.sub(r"<w:t>|<w:t xml:space=\"preserve\">", '<w:t xml:space="preserve">', raw)
    return raw


def _replace_in_paragraphs(raw: str, target: str, new: str, ext: str) -> tuple[str, int]:
    """Replace inside every paragraph of a block (a table holds many) without crossing paragraph boundaries."""
    pat = r"<w:p\b(?:(?!/>)[^>])*>.*?</w:p>" if ext == "docx" else r"<text:(p|h)\b(?:(?!/>)[^>])*>.*?</text:\1>"
    count = 0

    def one(m):
        nonlocal count
        para = m.group(0)
        start = 0
        while True:
            segs = _segments(para, ext)
            full = "".join(t for _, _, t in segs)
            hit = _find(full, target, start)
            if not hit:
                return para
            s0, e0 = hit
            old = full[s0:e0]
            # Change only the part that differs (replacing "suppliers shall be" with "suppliers must be" touches the
            # word shall alone), so the runs around it keep their own formatting.
            p = 0
            while p < min(len(old), len(new)) and old[p] == new[p]:
                p += 1
            q = 0
            while q < min(len(old), len(new)) - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
                q += 1
            para = _replace_once(para, segs, s0 + p, e0 - q, new[p:len(new) - q], ext)
            count += 1
            start = s0 + len(new)
            if not target:
                return para
    return re.sub(pat, one, raw, flags=re.S), count


# ---------------------------------------------------------------- applying to a text document
def _apply_text(doc: TextDoc, op: dict) -> tuple[bool, str]:
    o = op["op"]
    lines = op.get("lines") or []
    if o == "insert":
        if not lines:
            return False, "nothing to insert"
        new = doc.new_blocks(lines)
        lo, hi = doc.content_range()
        if op.get("at") == "start":
            at = lo
        elif op.get("at") == "end":
            at = hi
            if doc.ext == "docx" and hi - lo == 1 and doc.blocks[lo].kind == "p" and not doc.blocks[lo].text:
                at = lo  # an empty document: write into it rather than after its empty paragraph
        elif op.get("after") is not None:
            i = doc.find_block(op["after"])
            if i is None:
                return False, f"could not find “{_short(op['after'])}” to insert after"
            at = i + 1
        elif op.get("before") is not None:
            i = doc.find_block(op["before"])
            if i is None:
                return False, f"could not find “{_short(op['before'])}” to insert before"
            at = i
        else:
            i = doc.find_heading(op["section_end"])
            if i is None:
                return False, f"no section headed “{_short(op['section_end'])}”"
            at = doc.section_end(i)
        if doc.ext in ("md", "txt") and at > 0 and doc.blocks[at - 1].kind != "blank" and op.get("at") != "start":
            new = [Block("", "blank")] + new
        doc.insert(at, new)
        return True, f"inserted {len(lines)} line(s)"
    if o in ("replace", "delete"):
        target = op.get("target") or ""
        repl = "" if o == "delete" else (op.get("with") if op.get("with") is not None else None)
        if repl is None:
            return False, "no replacement text given (“@@ with: …”)"
        n = doc.inline_replace(target, repl, whole_para_removes=(repl == ""))
        if not n:
            return False, f"could not find “{_short(target)}”"
        return True, (f"removed it ({n} place(s))" if repl == "" else f"replaced {n} occurrence(s)")
    if o in ("delete_paragraph", "replace_paragraph"):
        i = doc.find_block(op["target"])
        if i is None:
            return False, f"could not find a paragraph with “{_short(op['target'])}”"
        if o == "replace_paragraph":
            if not lines:
                return False, "no new text for the paragraph"
            markers = doc._markers([doc.blocks[i]])
            del doc.blocks[i]
            doc.insert(i, doc.new_blocks(lines))
            doc._attach(markers, i + 1)
            return True, "replaced the paragraph"
        doc.remove(i, i + 1)
        if doc.ext in ("md", "txt") and i < len(doc.blocks) and doc.blocks[i].kind == "blank" and (i == 0 or doc.blocks[i - 1].kind == "blank"):
            del doc.blocks[i]
        return True, "deleted the paragraph"
    if o in ("delete_section", "replace_section"):
        i = doc.find_heading(op["target"])
        if i is None:
            return False, f"no section headed “{_short(op['target'])}”"
        j = doc.section_end(i)
        if o == "delete_section":
            doc.remove(i, j)
            return True, f"deleted the section ({j - i} block(s))"
        if not lines:
            return False, "no new body for the section"
        body = [x for x in lines]
        if body and squash(plain_line(body[0])) == squash(doc.blocks[i].text):
            body = body[1:]  # the model repeated the heading
        markers = doc._markers(doc.blocks[i + 1:j])
        del doc.blocks[i + 1:j]
        doc.insert(i + 1, doc.new_blocks(body) if doc.ext not in ("md", "txt") else [Block("", "blank")] + doc.new_blocks(body) + [Block("", "blank")])
        doc._attach(markers, i + 1)
        return True, "replaced the section's body"
    if o == "rewrite":
        if not lines:
            return False, "nothing to write"
        lo, hi = doc.content_range()
        markers = doc._markers(doc.blocks[lo:hi])
        del doc.blocks[lo:hi]
        doc.insert(lo, doc.new_blocks(lines))
        doc._attach(markers, lo + 1)
        return True, f"rewrote the document ({len(lines)} line(s))"
    return False, f"{describe(op)} cannot be done in a text document"


# ---------------------------------------------------------------- spreadsheets
def _cell_ref(ref: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-Z]{1,3})(\d+)", ref.upper())
    if not m:
        raise ValueError(f"not a cell: {ref}")
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col - 1


def _range(rng: str) -> tuple[int, int, int, int]:
    a, _, b = rng.partition(":")
    r1, c1 = _cell_ref(a)
    r2, c2 = _cell_ref(b or a)
    return min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2)


class Sheet:
    """The first sheet as {row: {col: cell xml}} (xlsx) or a list of expanded rows (ods)."""

    def __init__(self, content: bytes, ext: str):
        self.ext = ext
        z = _zip(content)
        if z is None:
            raise ValueError("not a zip package")
        self.names = z.namelist()
        self.files = {n: z.read(n) for n in self.names}
        self.has_formulas = False
        if ext == "xlsx":
            if "xl/worksheets/sheet1.xml" not in self.files:
                raise ValueError("no first sheet")
            self.xml = self.files["xl/worksheets/sheet1.xml"].decode("utf-8")
            self.shared = []
            if "xl/sharedStrings.xml" in self.files:
                ss = self.files["xl/sharedStrings.xml"].decode("utf-8")
                self.shared = [html.unescape("".join(re.findall(r"<t(?:\s[^>]*)?>(.*?)</t>", si, re.S))) for si in re.findall(r"<si>(.*?)</si>", ss, re.S)]
            m = re.search(r"<sheetData\s*/>|<sheetData>(.*?)</sheetData>", self.xml, re.S)
            self.rows: dict[int, dict] = {}
            for rm in re.finditer(r"<row\b([^>]*?)(?:/>|>(.*?)</row>)", (m.group(1) or "") if m else "", re.S):
                attrs, body = rm.group(1), rm.group(2) or ""
                rn = int(re.search(r'\br="(\d+)"', attrs).group(1))
                cells = {}
                for cm in re.finditer(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", body, re.S):
                    ref = re.search(r'\br="([A-Z]+\d+)"', cm.group(1)).group(1)
                    cells[_cell_ref(ref)[1]] = cm.group(0)
                    self.has_formulas |= "<f" in (cm.group(2) or "")
                self.rows[rn] = {"attrs": re.sub(r'\s*\br="\d+"', "", attrs), "cells": cells}
        elif ext == "ods":
            xml = self.files["content.xml"].decode("utf-8")
            m = re.search(r"<table:table\b[^>]*>.*?</table:table>", xml, re.S)
            if not m:
                raise ValueError("no table in the spreadsheet")
            self.xml, self.span = xml, (m.start(), m.end())
            table = m.group(0)
            open_tag = re.match(r"<table:table\b[^>]*>", table).group(0)
            inner = table[len(open_tag):-len("</table:table>")]
            self.open_tag = open_tag
            self.pre, self.ods_rows, self.tail = [], [], ""
            for a, b, name in _top_spans(inner):
                raw = inner[a:b]
                if name == "table:table-row":
                    rep = re.search(r'table:number-rows-repeated="(\d+)"', raw[: raw.find(">") + 1])
                    n = int(rep.group(1)) if rep else 1
                    if n > 2000:
                        self.tail += raw
                        continue
                    one = re.sub(r'\s*table:number-rows-repeated="\d+"', "", raw, count=1)
                    self.ods_rows += [one] * n
                    self.has_formulas |= "table:formula" in raw
                elif self.ods_rows or self.tail:
                    self.tail += raw
                else:
                    self.pre.append(raw)
        else:
            raise ValueError(f"cannot edit .{ext} sheets")

    # xlsx helpers
    def _xlsx_text(self, cell: str) -> str:
        if 't="s"' in cell:
            v = re.search(r"<v>(\d+)</v>", cell)
            return self.shared[int(v.group(1))] if v and int(v.group(1)) < len(self.shared) else ""
        if 't="inlineStr"' in cell:
            return html.unescape("".join(re.findall(r"<t(?:\s[^>]*)?>(.*?)</t>", cell, re.S)))
        v = re.search(r"<v>(.*?)</v>", cell, re.S)
        return html.unescape(v.group(1)) if v else ""

    @staticmethod
    def _xlsx_cell(ref: str, value: str, style: str = "") -> str:
        v = (value or "").strip()
        s = f' s="{style}"' if style else ""
        if v.startswith("=") and len(v) > 1:
            return f'<c r="{ref}"{s}><f>{_escape(v[1:])}</f></c>'
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            return f'<c r="{ref}"{s}><v>{v}</v></c>'
        return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{_escape(v)}</t></is></c>'

    @staticmethod
    def _ods_cell(value: str) -> str:
        v = (value or "").strip()
        if v.startswith("=") and len(v) > 1:
            return f'<table:table-cell table:formula="of:{_escape(v)}" office:value-type="float" office:value="0"><text:p>0</text:p></table:table-cell>'
        if re.fullmatch(r"-?\d+(\.\d+)?", v):
            return f'<table:table-cell office:value-type="float" office:value="{v}"><text:p>{v}</text:p></table:table-cell>'
        if not v:
            return "<table:table-cell/>"
        return f'<table:table-cell office:value-type="string"><text:p>{_escape(v)}</text:p></table:table-cell>'

    @staticmethod
    def _ods_cells(row: str) -> tuple[str, list[str], str]:
        """(row open tag, expanded cells, close) of an ods row; a huge trailing repeat stays one empty cell."""
        open_tag = re.match(r"<table:table-row\b[^>]*?/?>", row).group(0)
        if open_tag.endswith("/>"):
            return open_tag[:-2] + ">", [], "</table:table-row>"
        inner = row[len(open_tag):-len("</table:table-row>")]
        cells = []
        for a, b, _ in _top_spans(inner):
            raw = inner[a:b]
            rep = re.search(r'table:number-columns-repeated="(\d+)"', raw[: raw.find(">") + 1])
            n = int(rep.group(1)) if rep else 1
            one = re.sub(r'\s*table:number-columns-repeated="\d+"', "", raw, count=1)
            cells += [one] * min(n, 200)
        return open_tag, cells, "</table:table-row>"

    def grid(self) -> dict[tuple[int, int], str]:
        out = {}
        if self.ext == "xlsx":
            for rn, row in self.rows.items():
                for c, cell in row["cells"].items():
                    t = self._xlsx_text(cell)
                    if t != "":
                        out[(rn, c)] = t
        else:
            for i, row in enumerate(self.ods_rows):
                _, cells, _ = self._ods_cells(row)
                for c, cell in enumerate(cells):
                    t = _odt_text(cell) or (re.search(r'office:value="([^"]*)"', cell) or [None, ""])[1]
                    if t:
                        out[(i + 1, c)] = t
        return out

    def set(self, rn: int, c: int, value: str) -> None:
        ref = f"{_col(c)}{rn}"
        if self.ext == "xlsx":
            row = self.rows.setdefault(rn, {"attrs": "", "cells": {}})
            old = row["cells"].get(c, "")
            style = (re.search(r'\bs="(\d+)"', old) or [None, ""])[1] if old else ""
            if value == "":
                row["cells"].pop(c, None)
            else:
                row["cells"][c] = self._xlsx_cell(ref, value, style)
            return
        while len(self.ods_rows) < rn:
            self.ods_rows.append("<table:table-row><table:table-cell/></table:table-row>")
        open_tag, cells, close = self._ods_cells(self.ods_rows[rn - 1])
        while len(cells) <= c:
            cells.append("<table:table-cell/>")
        cells[c] = self._ods_cell(value)
        while len(cells) > 1 and cells[-1] in ("<table:table-cell/>",):
            cells.pop()
        self.ods_rows[rn - 1] = open_tag + "".join(cells) + close

    def last_row(self) -> int:
        if self.ext == "xlsx":
            used = [rn for rn, r in self.rows.items() if r["cells"]]
            return max(used) if used else 0
        used = [i + 1 for i, r in enumerate(self.ods_rows) if "<text:p" in r or "office:value=" in r]
        return max(used) if used else 0

    def delete_rows(self, a: int, b: int) -> int:
        k = b - a + 1
        if self.ext == "xlsx":
            gone = sum(1 for rn in self.rows if a <= rn <= b)
            new = {}
            for rn, row in self.rows.items():
                if a <= rn <= b:
                    continue
                nr = rn - k if rn > b else rn
                if nr != rn:
                    row["cells"] = {c: re.sub(r'\br="[A-Z]+\d+"', f'r="{_col(c)}{nr}"', x, count=1) for c, x in row["cells"].items()}
                new[nr] = row
            self.rows = new
            return gone
        gone = len(self.ods_rows[a - 1:b])
        del self.ods_rows[a - 1:b]
        return gone

    def insert_rows(self, at: int, rows: list[list[str]]) -> None:
        k = len(rows)
        if self.ext == "xlsx":
            new = {}
            for rn, row in sorted(self.rows.items()):
                nr = rn + k if rn >= at else rn
                if nr != rn:
                    row["cells"] = {c: re.sub(r'\br="[A-Z]+\d+"', f'r="{_col(c)}{nr}"', x, count=1) for c, x in row["cells"].items()}
                new[nr] = row
            self.rows = new
            for i, cells in enumerate(rows):
                for c, v in enumerate(cells):
                    self.set(at + i, c, v)
            return
        while len(self.ods_rows) < at - 1:
            self.ods_rows.append("<table:table-row><table:table-cell/></table:table-row>")
        block = ["<table:table-row>" + "".join(self._ods_cell(v) for v in cells) + "</table:table-row>" for cells in rows]
        self.ods_rows[at - 1:at - 1] = block

    def to_bytes(self) -> bytes:
        if self.ext == "xlsx":
            rows = "".join(f'<row r="{rn}"{row["attrs"]}>' + "".join(row["cells"][c] for c in sorted(row["cells"])) + "</row>"
                           for rn, row in sorted(self.rows.items()) if row["cells"] or row["attrs"].strip())
            xml = re.sub(r"<sheetData\s*/>|<sheetData>.*?</sheetData>", lambda m: f"<sheetData>{rows}</sheetData>", self.xml, count=1, flags=re.S)
            xml = re.sub(r"<dimension\b[^>]*/>", "", xml, count=1)
            self.files["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
        else:
            table = self.open_tag + "".join(self.pre) + "".join(self.ods_rows) + self.tail + "</table:table>"
            a, b = self.span
            self.files["content.xml"] = (self.xml[:a] + table + self.xml[b:]).encode("utf-8")
        return _rezip(self.names, self.files)


def _apply_sheet(sheet: Sheet, op: dict) -> tuple[bool, str]:
    o = op["op"]
    if o in ("append_rows", "insert"):
        rows = rows_from_lines(op.get("lines") or [])
        if not rows:
            return False, "no rows given"
        if o == "insert" and op.get("at") == "start":
            sheet.insert_rows(1, rows)
            return True, f"inserted {len(rows)} row(s) at the top"
        start = sheet.last_row() + 1
        for i, cells in enumerate(rows):
            for c, v in enumerate(cells):
                sheet.set(start + i, c, v)
        return True, f"appended {len(rows)} row(s) from row {start}"
    if o == "insert_rows":
        rows = rows_from_lines(op.get("lines") or [])
        if not rows:
            return False, "no rows given"
        sheet.insert_rows(int(op["at"]), rows)
        return True, f"inserted {len(rows)} row(s) at row {op['at']}" + (" (formulas were not adjusted)" if sheet.has_formulas else "")
    if o == "set":
        rn, c = _cell_ref(op["cell"])
        sheet.set(rn, c, op.get("value") or "")
        return True, f"set {op['cell']}"
    if o == "clear":
        r1, c1, r2, c2 = _range(op["range"])
        n = 0
        grid = sheet.grid()
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) in grid:
                    sheet.set(r, c, "")
                    n += 1
        return True, f"cleared {n} cell(s)"
    if o == "delete_rows":
        a, b = sorted((int(op["from"]), int(op["to"])))
        n = sheet.delete_rows(a, b)
        return (n > 0), (f"deleted {n} row(s)" + (" (formulas were not adjusted)" if sheet.has_formulas else "")) if n else f"rows {a}-{b} are empty already"
    if o in ("replace", "delete"):
        target, new = op.get("target") or "", ("" if o == "delete" else op.get("with"))
        if new is None:
            return False, "no replacement text given (“@@ with: …”)"
        n = 0
        for (r, c), t in sheet.grid().items():
            hit = _find(t, target)
            if hit:
                sheet.set(r, c, re.sub(re.escape(t[hit[0]:hit[1]]), lambda m: new, t))
                n += 1
        return (n > 0), (f"changed {n} cell(s)" if n else f"could not find “{_short(target)}” in any cell")
    return False, f"{describe(op)} cannot be done in a spreadsheet"


# ---------------------------------------------------------------- the entry points
def apply_ops(content: bytes, ext: str, ops: list[dict]) -> tuple[bytes | None, list[dict]]:
    """Apply the operations in order. Returns (new content or None when nothing changed, a report per operation)."""
    ext = (ext or "").lower()
    report: list[dict] = []
    if is_sheet(ext):
        if ext not in ("xlsx", "ods"):
            return None, [{"op": describe(o), "ok": False, "detail": f"cannot edit .{ext} files"} for o in ops]
        if not ops:
            return None, []
        try:
            target = Sheet(content, ext)
        except ValueError as e:
            return None, [{"op": describe(o), "ok": False, "detail": str(e)} for o in ops]
        apply = _apply_sheet
    else:
        if ext not in ("docx", "odt", "md", "txt"):
            return None, [{"op": describe(o), "ok": False, "detail": f"cannot edit .{ext} files"} for o in ops]
        try:
            target = TextDoc(content, ext)
        except ValueError as e:
            return None, [{"op": describe(o), "ok": False, "detail": str(e)} for o in ops]
        apply = _apply_text
    changed = False
    for op in ops:
        try:
            ok, detail = apply(target, op)
        except Exception as e:  # one bad operation never spoils the others
            ok, detail = False, f"{type(e).__name__}: {e}"
        report.append({"op": describe(op), "ok": ok, "detail": detail})
        changed |= ok
    return (target.to_bytes() if changed else None), report


def sheet_grid_text(content: bytes, ext: str, limit: int = 60) -> str:
    """The first sheet with cell addresses, for a cog that edits it: 'row 3: A=Item | B=Limit'."""
    try:
        grid = Sheet(content, ext.lower()).grid()
    except Exception:
        return ""
    rows: dict[int, list[str]] = {}
    for (r, c), t in sorted(grid.items()):
        rows.setdefault(r, []).append(f"{_col(c)}={_short(t, 40)}")
    lines = [f"row {r}: " + " | ".join(cells) for r, cells in sorted(rows.items())[:limit]]
    return "\n".join(lines) if lines else "(the sheet is empty)"


# ---------------------------------------------------------------- did a live edit take effect?
def op_applied(op: dict, text: str, grid: dict | None = None) -> bool:
    """Whether an operation shows in the document's text after a live edit (so the hub need not do it again)."""
    t = squash(text)
    o = op["op"]
    first = next((plain_line(x) for x in op.get("lines") or [] if plain_line(x)), "")
    if o in ("insert", "replace_paragraph", "replace_section", "rewrite"):
        ok = bool(first) and squash(first) in t
        if o == "replace_paragraph" and squash(op["target"]) not in squash(" ".join(plain_line(x) for x in op.get("lines") or [])):
            ok = ok and squash(op["target"]) not in t
        return ok
    if o in ("replace", "delete"):
        new = "" if o == "delete" else (op.get("with") or "")
        gone = squash(op["target"]) not in t or (new and squash(op["target"]) in squash(new))
        return bool(gone) and (not new or squash(new) in t)
    if o == "delete_paragraph":
        return squash(op["target"]) not in t
    if o == "delete_section":
        return squash(op["target"]) not in t
    if grid is not None:
        if o == "set":
            rn, c = _cell_ref(op["cell"])
            return squash(grid.get((rn, c), "")) == squash(op.get("value") or "") or (str(op.get("value") or "").startswith("=") and (rn, c) in grid)
        if o == "append_rows":
            rows = rows_from_lines(op.get("lines") or [])
            vals = {squash(v) for v in grid.values()}
            return all(squash(v) in vals for r in rows for v in r if v.strip() and not v.strip().startswith("="))
    return False


def live_capable(edit: dict) -> bool:
    """Operations the cog's seat performs live in the editor; anything else is done in the file by the hub."""
    ops = edit.get("ops") or []
    if not ops:
        return False
    if edit.get("kind") == "sheet":
        return all(o["op"] in ("append_rows", "set") for o in ops)
    for o in ops:
        if o["op"] in ("replace", "delete", "delete_paragraph"):
            continue
        if o["op"] == "insert" and not any(re.match(r"^\s*(\||[-*•]\s|\d+[.)]\s|---\s*$)", x) for x in o.get("lines") or []):
            continue
        return False
    return True


def applied_text(content: bytes, ext: str) -> str:
    return extract_text(content, ext, limit=200000)
