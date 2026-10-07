"""Shared precise image-reference edits; recognition markup is never rendered."""
import re


def _replace_markdown_images(markdown_text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return markdown_text
    from html import escape
    updated = markdown_text
    for start, end, ref, html_attribute in reversed(_image_reference_spans(markdown_text)):
        if ref in replacements:
            if isinstance(html_attribute, tuple):
                action, title = html_attribute
                title_suffix = ' "' + title.replace("\\", "\\\\").replace('"', '\\"') + '"' if title else ""
                value = "(<" + replacements[ref] + ">" + title_suffix + ")" if action == "reference_insert" else ""
            else:
                value = escape(replacements[ref], quote=True) if html_attribute else replacements[ref]
            updated = updated[:start] + value + updated[end:]
    return updated


def _extract_markdown_images(markdown_text: str) -> list[str]:
    return [ref for _, _, ref, _ in _image_reference_spans(markdown_text or "")]


def _image_reference_spans(text: str):
    from html import unescape
    from html.parser import HTMLParser
    from markdown_it import MarkdownIt
    from markdown_it.rules_inline.image import image
    from markdown_it.rules_inline.html_inline import html_inline
    from markdown_it.common.utils import isStrSpace

    spans = []
    raw_lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in raw_lines:
        offsets.append(offsets[-1] + len(line))
    active = None
    active_source = None

    def source_positions(token):
        # Token line maps constrain alignment; no Markdown grammar is inferred here.
        positions = []
        cursor = token.map[0]
        for line in token.content.splitlines(keepends=True):
            value = line.rstrip("\n")
            found = False
            while cursor < token.map[1]:
                raw = raw_lines[cursor].rstrip("\r\n")
                start = raw.find(value)
                virtual_indent = 0
                if start < 0:
                    content = value.lstrip(" ")
                    virtual_indent = len(value) - len(content)
                    start = raw.find(content) if content else -1
                    value = content
                if start >= 0:
                    if start != raw.rfind(value) and value:
                        raise ValueError("ambiguous parser source position")
                    positions.extend([None] * virtual_indent)
                    positions.extend(range(offsets[cursor] + start, offsets[cursor] + start + len(value)))
                    if line.endswith("\n"):
                        positions.append(offsets[cursor] + len(raw))
                    cursor += 1
                    found = True
                    break
                cursor += 1
            if not found:
                raise ValueError("parser source position unavailable")
        return positions

    def record(start, end, ref, html_attribute):
        if not active or end <= start:
            return
        positions = active[start:end]
        if len(positions) != end - start or None in positions or any(b != a + 1 for a, b in zip(positions, positions[1:])):
            raise ValueError("noncontiguous asset source position")
        spans.append((positions[0], positions[-1] + 1, ref, html_attribute))

    def record_reference(label_end, consumed_end, ref, title):
        closing = active[label_end]
        if closing is None or text[closing] != "]":
            raise ValueError("reference image closing position unavailable")
        spans.append((closing + 1, closing + 1, ref, ("reference_insert", title)))
        # Remove only parser-consumed reference syntax; retain raw line endings
        # and container prefixes that are absent from the normalized inline source.
        for index in range(label_end + 1, consumed_end):
            if active_source[index] == "\n":
                continue
            position = active[index]
            if position is None or text[position] != active_source[index]:
                raise ValueError("reference suffix source position unavailable")
            spans.append((position, position + 1, ref, ("reference_delete", "")))

    def html_refs(raw, base):
        class Images(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag != "img":
                    return
                if sum(name == "src" for name, _ in attrs) > 1:
                    raise ValueError("ambiguous HTML image src")
                line, column = self.getpos()
                tag_start = sum(len(part) for part in raw.splitlines(keepends=True)[:line-1]) + column
                for match in re.finditer(r"""\s+([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?""", self.get_starttag_text()):
                    if match.group(1).lower() != "src":
                        continue
                    group = next((i for i in (2, 3, 4) if match.group(i) is not None), None)
                    if group is None:
                        raise ValueError("HTML image src requires a value")
                    record(base + tag_start + match.start(group), base + tag_start + match.end(group), unescape(match.group(group)), True)
        Images(convert_charrefs=False).feed(raw)

    def owned_image(state, silent):
        start = state.pos
        before = len(state.tokens)
        accepted = image(state, silent)
        if accepted and not silent and state.src is active_source:
            label_end = state.md.helpers.parseLinkLabel(state, start + 1, False)
            pos = label_end + 1
            if pos >= state.posMax or state.src[pos] != "(":
                token = next(token for token in state.tokens[before:] if token.type == "image")
                record_reference(label_end, state.pos, token.attrGet("src"), token.attrGet("title") or "")
                return accepted
            pos += 1
            while pos < state.posMax and (isStrSpace(state.src[pos]) or state.src[pos] == "\n"):
                pos += 1
            destination = state.md.helpers.parseLinkDestination(state.src, pos, state.posMax)
            if not destination.ok:
                raise ValueError("accepted image destination position unavailable")
            angled = state.src[pos] == "<"
            record(pos + int(angled), destination.pos - int(angled), destination.str, False)
        return accepted

    def owned_html(state, silent):
        start = state.pos
        accepted = html_inline(state, silent)
        if accepted and not silent and state.src is active_source:
            html_refs(state.src[start:state.pos], start)
        return accepted

    def parse_inlines(state):
        nonlocal active, active_source
        for token in state.tokens:
            if token.type not in ("inline", "html_block"):
                continue
            active = source_positions(token)
            active_source = token.content
            if token.type == "html_block":
                html_refs(token.content, 0)
            else:
                token.children = []
                state.md.inline.parse(token.content, state.md, state.env, token.children)
        active = None
        active_source = None

    parser = MarkdownIt("commonmark", {"html": True, "store_labels": True})
    parser.inline.ruler.at("image", owned_image)
    parser.inline.ruler.at("html_inline", owned_html)
    parser.core.ruler.at("inline", parse_inlines)
    parser.parse(text)
    return sorted(set(spans))
