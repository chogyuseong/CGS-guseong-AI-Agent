"""메일 본문 다루기(7단계). 표준 라이브러리만 쓴다(base64, html.parser). 새 의존성·실행 파일이 없다.

Gmail API의 format=full 메시지(공식 문서의 Message 리소스)는 payload가 MIME 부분 트리다:
    payload {mimeType, filename, headers[{name, value}], body {size, data(base64url), attachmentId}, parts[...]}
- 본문: 첨부가 아닌(filename 없음) text/plain을 먼저 쓰고, 없으면 text/html을 텍스트로 바꿔 쓴다.
- 첨부: filename이 있는 부분의 이름만 모은다. 첨부 내용(attachmentId)은 내려받지 않는다(명세: 첨부는 이름만).
- 트리는 깊이 10, 부분 200개까지만 본다(이상하게 큰 메일이 서버를 붙잡지 않게).

HTML → 텍스트(html_to_text): script·style·head(title 포함)·noscript·template·svg 안의 글자는 버리고, 주석은 버리고,
태그는 지우고, 블록 태그(p, div, br, li, tr, h1~h6 …)는 줄바꿈으로 바꾸고, &amp; 같은 문자 참조는 글자로 되돌린다.
보이지 않게 숨긴 글(display:none 등)은 걸러내지 못한다. 메일 내용은 어차피 지시문 무시 안내로 감싸 넘긴다(절대 원칙 7).

위험 등급: 없음(외부 호출·DB 없음, 순수 함수). 도구가 아니라 app/tools/gmail.py의 도우미다.
진입점: gmail.message_view → extract_body, gmail.summary_view → header_value, gmail이 MAX_ATTACHMENTS를 쓴다.
근거: 명세 7단계(본문 4,000자·첨부 이름만), DECISIONS "API(메일 읽기)" 2026-11-17.
변경 이력: 2026-11-17 7단계에서 만듦.

고칠 때 주의:
- 새 의존성(HTML 파서 라이브러리 등)을 넣지 않는다(DECISIONS 2026-11-17: 표준 라이브러리 html.parser로 함).
  네이티브 확장을 내려받는 패키지는 Smart App Control에 막힐 수 있다(AGENTS.md).
- 결과 모양((본문, 첨부 이름 목록))을 바꾸면 gmail.message_view와 docs/TOOLS.md Gmail 절을 고친다.
- 형식 응답: tests/fixtures/gmail_message_html.json(script·style·주석), gmail_message_attachment.json(multipart·첨부),
  gmail_message_injection.json(text/plain). 테스트: tests/test_gmail.py.
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
# 아래 넷은 BUILD_SPEC에 수치가 없어 정한 값이다(DECISIONS "API(메일 읽기)" 2026-11-17).
# 깊이·부분 수 제한은 이상하게 크거나 깊은 메일이 서버를 붙잡지 않게 한다(파일 머리 설명).
MAX_PART_DEPTH = 10
MAX_PARTS = 200
MAX_ATTACHMENTS = 20  # 모델에게 넘길 첨부 이름 수. 넘으면 gmail.message_view가 전체 수만 따로 알린다
ATTACHMENT_NAME_LIMIT = 100
# Content-Type 헤더의 charset 이름(영숫자·._-만). 파이썬이 모르는 이름이면 decode_body가 UTF-8로 푼다.
CHARSET = re.compile(r"charset\s*=\s*\"?([A-Za-z0-9._-]+)\"?", re.IGNORECASE)
# 줄바꿈(\n)을 뺀 제어 문자, 폭 없는 문자·방향 표시(U+200B~200F), 줄·문단 구분자(U+2028·2029), BOM(U+FEFF).
# 공백으로 바꾼다. 고칠 때 주의: 이 줄에는 그 글자들이 그대로 들어 있어 편집기가 줄을 나눠 보일 수 있다.
CONTROL_EXCEPT_NEWLINE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f​-‏  ﻿]")
SPACES = re.compile(r"[ \t\xa0　]+")
MANY_NEWLINES = re.compile(r"\n{3,}")
BLANK_LINES = re.compile(r"\n{2,}")


class TextExtractor(HTMLParser):
    """HTML에서 보이는 글자만 모은다. 건너뛸 태그 안이면 글자를 버린다.

    convert_charrefs=True라 &amp; 같은 문자 참조는 handle_data에 글자로 온다. 주석은 handle_comment를
    두지 않아 버려진다. skip_depth는 겹친 건너뛸 태그를 세고, 짝 없는 닫는 태그로 음수가 되지 않게 0에서 멈춘다.
    """

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
    """text/html 본문을 보이는 글자로. 링크 주소·이미지 대체 글 같은 속성 값은 넣지 않는다(글자 노드만 모은다)."""
    parser = TextExtractor()
    parser.feed(html)
    parser.close()
    return BLANK_LINES.sub("\n", tidy("".join(parser.chunks))).strip()  # 블록 태그가 겹쳐 생긴 빈 줄은 하나로


def header_value(part: dict[str, Any], name: str) -> str:
    """부분의 헤더 값(이름은 대소문자 무시). 없으면 빈 문자열. 같은 이름이 여럿이면 첫 값.

    gmail.summary_view(From·Subject)와 decode_body(Content-Type)가 쓴다. 값은 외부 글이라 쓰는 쪽이 한 줄로 다듬는다.
    """
    for header in part.get("headers") or []:
        if isinstance(header, dict) and str(header.get("name", "")).lower() == name.lower():
            return str(header.get("value") or "")
    return ""


def decode_body(part: dict[str, Any]) -> str:
    """body.data(base64url)를 부분의 charset으로 풀어 글자로. 풀 수 없으면 빈 문자열.

    패딩(=)이 빠져 와도 풀리게 길이를 4의 배수로 채운다. 깨진 글자는 대체 문자로 두고(errors="replace"),
    모르는 charset은 UTF-8로 푼다. 예외를 밖으로 던지지 않아 이상한 메일 하나가 read_mail을 실패시키지 않는다.
    """
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
    """부분 트리를 앞에서부터(문서 순서) 펼친다. 깊이 10, 200개까지.

    스택으로 돈다. 자식을 거꾸로 넣어야 꺼낼 때 문서 순서가 된다. 리스트·dict가 아닌 값은 건너뛴다.
    문서 순서가 중요한 이유: first_text가 앞에 나온 본문을 고른다(multipart/alternative의 첫 text/plain).
    """
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
    """mime_type인 첫 본문(filename이 있는 첨부 텍스트 파일은 빼고, 빈 글은 건너뛴다). 없으면 빈 문자열."""
    for part in parts:
        if part.get("mimeType") == mime_type and not part.get("filename"):
            text = decode_body(part)
            if text.strip():
                return text
    return ""


def attachment_names(parts: list[dict[str, Any]]) -> list[str]:
    """첨부 파일 이름 전체(한 줄, 100자). 수 제한(MAX_ATTACHMENTS)은 gmail.message_view가 건다(전체 수를 알리려고).
    내용(attachmentId)은 쓰지 않는다. 파일 이름도 보낸 사람이 정한 외부 글이다."""
    names = [tidy(str(part["filename"])).replace("\n", " ") for part in parts if part.get("filename")]
    return [name[:ATTACHMENT_NAME_LIMIT] for name in names if name]


def extract_body(payload: Any) -> tuple[str, list[str]]:
    """(본문 텍스트, 첨부 이름 전체 목록). text/plain을 먼저, 없으면 text/html을 텍스트로 바꾼다.

    본문은 자르지 않고 돌려준다(4,000자 자르기와 잘림 표시는 gmail.message_view). 본문이 없으면 "".
    """
    parts = flatten_parts(payload)
    plain = first_text(parts, "text/plain")
    body = tidy(plain) if plain else html_to_text(first_text(parts, "text/html"))
    return body, attachment_names(parts)
