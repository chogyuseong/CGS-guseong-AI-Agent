"""메일 본문 다루기(7단계). 표준 라이브러리만 쓴다(base64, html.parser). 새 의존성·실행 파일이 없다.

Gmail API의 format=full 메시지(공식 문서의 Message 리소스)는 payload가 MIME 부분 트리다:
    payload {mimeType, filename, headers[{name, value}], body {size, data(base64url), attachmentId}, parts[...]}
- 본문: 첨부가 아닌(filename 없음) text/plain을 먼저 쓰고, 없으면 text/html을 텍스트로 바꿔 쓴다.
- 첨부: filename이 있는 부분의 이름만 모은다. 첨부 내용(attachmentId)은 내려받지 않는다(명세: 첨부는 이름만).
- 트리는 깊이 10, 부분 200개까지만 본다(이상하게 큰 메일이 서버를 붙잡지 않게).

HTML → 텍스트(html_to_text): script·style·head(title 포함)·noscript·template·svg 안의 글자는 버리고, 주석은 버리고,
태그는 지우고, 블록 태그(p, div, br, li, tr, h1~h6 …)는 줄바꿈으로 바꾸고, &amp; 같은 문자 참조는 글자로 되돌린다.
보이지 않게 숨긴 글(display:none 등)은 걸러내지 못한다. 메일 내용은 어차피 지시문 무시 안내로 감싸 넘긴다(절대 원칙 7).
"""

import base64
import binascii
import re
from html.parser import HTMLParser
from typing import Any

SKIPPED_TAGS = frozenset({"script", "style", "head", "title", "noscript", "template", "svg"})
BLOCK_TAGS = frozenset(
    {"br", "p", "div", "li", "tr", "table", "ul", "ol", "blockquote", "section", "article", "header", "footer", "hr"}
    | {f"h{level}" for level in range(1, 7)}
)
MAX_PART_DEPTH = 10
MAX_PARTS = 200
MAX_ATTACHMENTS = 20
ATTACHMENT_NAME_LIMIT = 100
CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9._-]+)\"?", re.IGNORECASE)
CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f​-‏  ﻿]")
SPACES = re.compile(r"[ \t\xa0　]+")
MANY_NEWLINES = re.compile(r"\n{3,}")
BLANK_LINES = re.compile(r"\n{2,}")


class TextExtractor(HTMLParser):
    """HTML에서 보이는 글자만 모은다. 건너뛸 태그 안이면 글자를 버린다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self.skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            self.chunks.append(data)


def tidy(text: str) -> str:
    """줄바꿈을 \\n으로 맞추고, 줄마다 공백을 하나로, 빈 줄은 두 줄까지만 남긴다. 제어 문자·폭 없는 문자는 지운다."""
    text = CONTROL_EXCEPT_NEWLINE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return MANY_NEWLINES.sub("\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    parser.close()
    return BLANK_LINES.sub("\n", tidy("".join(parser.chunks))).strip()  # 블록 태그가 겹쳐 생긴 빈 줄은 하나로


def header_value(part: dict[str, Any], name: str) -> str:
    """부분의 헤더 값(이름은 대소문자 무시). 없으면 빈 문자열."""
    for header in part.get("headers") or []:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value") or "")
    return ""


def decode_body(part: dict[str, Any]) -> str:
    """body.data(base64url)를 부분의 charset으로 풀어 글자로. 풀 수 없으면 빈 문자열."""
    data = (part.get("body") or {}).get("data")
    if not isinstance(data, str) or not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError):
        return ""
    match = CHARSET.search(header_value(part, "Content-Type"))
    try:
        return raw.decode(match.group(1) if match else "utf-8", errors="replace")
    except LookupError:  # 모르는 charset 이름
        return raw.decode("utf-8", errors="replace")


def flatten_parts(payload: Any) -> list[dict[str, Any]]:
    """부분 트리를 앞에서부터(문서 순서) 펼친다. 깊이 10, 200개까지."""
    flat: list[dict[str, Any]] = []
    stack: list[tuple[Any, int]] = [(payload, 0)]
    while stack and len(flat) < MAX_PARTS:
        part, depth = stack.pop()
        if not isinstance(part, dict):
            continue
        flat.append(part)
        children = part.get("parts")
        if isinstance(children, list) and depth < MAX_PART_DEPTH:
            stack.extend((child, depth + 1) for child in reversed(children))
    return flat


def first_text(parts: list[dict[str, Any]], mime_type: str) -> str:
    for part in parts:
        if part.get("mimeType") == mime_type and not part.get("filename"):
            text = decode_body(part)
            if text.strip():
                return text
    return ""


def attachment_names(parts: list[dict[str, Any]]) -> list[str]:
    names = [tidy(str(part["filename"])).replace("\n", " ") for part in parts if part.get("filename")]
    return [name[:ATTACHMENT_NAME_LIMIT] for name in names if name]


def extract_body(payload: Any) -> tuple[str, list[str]]:
    """(본문 텍스트, 첨부 이름 전체 목록). text/plain을 먼저, 없으면 text/html을 텍스트로 바꾼다."""
    parts = flatten_parts(payload)
    plain = first_text(parts, "text/plain")
    body = tidy(plain) if plain else html_to_text(first_text(parts, "text/html"))
    return body, attachment_names(parts)
