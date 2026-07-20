"""
scripts/extract_detail_texts.py
================================
원본 PDF(jeju_olle_guidebook.pdf)에서 코스별 [3페이지 이후 상세 코스 정보] 텍스트를
좌우 2단(실제로는 2페이지 스프레드) 레이아웃을 컬럼 단위로 분리 추출한 뒤,
정규식 기반 노이즈/스페셜 정보 제거를 수행하여 임베딩용 데이터 파일로 저장합니다.

핵심 포인트
- PDF 한 페이지는 실제 책의 좌/우 두 페이지가 나란히 스캔된 스프레드이다.
  기존 pdfplumber.extract_text()는 좌우 컬럼 텍스트를 줄 단위로 섞어버려
  문장이 중간에 끊기고 다른 문장과 뒤섞이는 문제가 있었다.
  이를 해결하기 위해 페이지를 폭의 절반 지점에서 좌/우로 crop 한 뒤 각각
  extract_text()를 호출한다.
- 각 페이지 좌/우 컬럼 상단에는 코스 제목/부제목(러닝헤더)과, 본문 중간에는
  일화/명소 소개 박스 제목이 반복 삽입되는데, 이들은 본문(7~8pt)보다 글자
  크기가 뚜렷이 큰(약 10pt 이상) 별도 폰트를 사용한다. 이 폰트 크기 차이를
  이용해 제목성 라인을 우선 제거한다.
- 말풍선/둥근 테두리 박스로 그려진 명소·일화 소개 카드는 벡터 곡선(curve)으로
  그려져 있어 page.rects 로는 잡히지 않는다. page.curves 좌표를 클러스터링해
  박스 영역을 찾아낸 뒤, 해당 좌표 범위의 텍스트를 컬럼 크롭 단계에서
  outside_bbox() 로 통째로 제외한다(본문과 같은 폰트라 라인/폰트 필터만으로는
  구분할 수 없기 때문).
- 축제/이벤트 안내, 체험 프로그램 홍보, 제주어 학습 코너, 시설 입장료·연락처
  등 "스페셜 정보"는 기획서(hybrid_rag_mvp_plan.md)에서 정의한 MVP 범위 제외
  대상이므로 규칙 기반으로 마저 제거한다.

LLM 미사용 — 결정적(deterministic)이고 재현 가능한 규칙 기반 정제만 수행합니다.
(이전 Upstage Solar LLM 정제 단계는 뒤섞인 원문 입력으로 인해 일부 코스에서
반복 루프/메타 코멘트 누출 등 심각한 환각이 발생하여 제외하였습니다.)

출력:
  data/extracted/course_detail_texts.json   - 코스별 상세 텍스트 JSON
  data/extracted/course_detail_texts.csv    - 임베딩 적재용 CSV (utf-8-sig)
"""

import csv
import json
import os
import re
import sys

import pdfplumber

# 프로젝트 루트 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ingestion.parser import extract_course_name_from_first_page  # noqa: E402

PDF_PATH = "data/raw_data/jeju_olle_guidebook.pdf"
START_PAGE = 27
END_PAGE = 134
OUTPUT_JSON = "data/extracted/course_detail_texts.json"
OUTPUT_CSV = "data/extracted/course_detail_texts.csv"

# 본문(7~8pt)보다 뚜렷이 큰 러닝헤더/박스제목 폰트를 걸러내는 임계값
HEADER_FONT_SIZE_THRESHOLD = 9.5
# 라인 내 단어 중 이 비율 이상이 "제목 폰트" 단어면 해당 라인 전체를 제거
HEADER_LINE_WORD_RATIO = 0.6

# 말풍선/박스형 스페셜 정보(명소 소개, 일화 등) 그래픽 영역 탐지 — 벡터 곡선(curve)으로
# 그려진 둥근 테두리 박스를 좌표 기반으로 찾아 해당 영역의 텍스트를 통째로 제외한다.
BOX_MIN_AREA = 3000  # 이보다 작은 곡선 뭉치는 장식용 아이콘 등으로 보고 무시
BOX_MERGE_GAP = 10  # 이 거리(pt) 이내의 곡선들은 같은 박스(말풍선 꼬리 포함)로 병합
SIDEBAR_MARGIN_WIDTH = 45  # 페이지 우측 끝 "Jeju Olle Route" 세로 장식 바 폭 — 박스 탐지에서 제외

SEPARATOR = "---"

# ────────────────────────────────────────────────
# 노이즈 제거 규칙 (라인 단위 필터) — 스페셜 정보(축제/체험/제주어 코너 등) 포함
# ────────────────────────────────────────────────

LINE_EXACT_REMOVE_PATTERNS = [
    re.compile(r"^\d{1,3}\s*$"),  # 단독 숫자 (페이지 번호)
    re.compile(r"^etuoR\s*$"),  # 역방향 워터마크
    re.compile(r"^ellO\s*$"),
    re.compile(r"^ujeJ\s*$"),
    re.compile(r"^\d+-?\d*\s*0\s*$"),
    re.compile(r"^\+\s*\+\s*$"),
    re.compile(r"^덤\s*하나\b"),
    re.compile(r"^덤\s*둘\b"),
    re.compile(r"^덤\s*셋\b"),
    re.compile(r"^\+\s*덤\s*하나\b"),
    re.compile(r"^\+\s*덤\s*둘\b"),
    re.compile(r"^\+\s*덤\s*셋\b"),
    # 러닝헤더 잔여물: "01 시흥 - 광치기 올레" 형태의 코스 제목 라인
    re.compile(r"^\d{1,2}(-\d+)?\s+\S.*올레\s*$"),
]

LINE_CONTAINS_REMOVE = [
    "etuoR ellO ujeJ",
    "저작권법에 의거",
    "Copyright ⓒ",
    "BB PPoo aann gguu ee ss",
]

# 학습/체험 코너 장식 배지 (예: "BB PPoo aann gguu ee e", "BB PPoo aann gguu ee s s 11")
# — OCR 렌더링이 조금씩 달라지므로 "짧은 라틴 문자 토큰이 반복되는 구간" 패턴으로 탐지한다.
# 배지가 문단 중간에 끼어 한 줄로 합쳐져 추출되는 경우가 있어 줄 전체가 아니라
# 텍스트 내 어디서든 검색(search)한 뒤, 그 지점부터 컬럼의 나머지를 통째로 잘라낸다.
LATIN_BADGE_RE = re.compile(r"(?:\b[A-Za-z]{1,4}\b\s+){3,}[A-Za-z]{0,4}\s*\d{0,3}")

SIDEBAR_BLOCK_START_PATTERNS = [
    re.compile(r"^기간\s+\d"),
    re.compile(r"^기간\s+[가-힣]"),
    re.compile(r"^\s*기간\s+\d"),
    re.compile(r"^\s*기간\s+[가-힣]"),
    re.compile(r"반갑수다예"),
    re.compile(r"고맙수다예"),
    re.compile(r"삼춘,\s*어디\s*감수광"),
    re.compile(r"제주올레\s*길에서\s*제주어"),
    re.compile(r"사람들과\s*인사할\s*때"),
    re.compile(r"길을\s*물어볼\s*때"),
    re.compile(r"체험\s*놀거리"),
    re.compile(r"우리\s*모영\s*놀게"),
    re.compile(r"세계\s*최초의\s*전문직\s*여성\s*,\s*해녀"),
]

SIDEBAR_TITLE_RE = re.compile(
    r"^(기간|장소|내용|문의|입장료|홈페이지|전화|주소|운영|참가비|예약)\s*[:：]"
)

# ────────────────────────────────────────────────
# 문장 단위 운영정보(입장료/연락처/URL 등) 제거 — 명소 소개 박스에 중복 삽입된
# 시설 운영정보만 골라내며, 유래/역사/전설 서술은 그대로 보존한다.
# ────────────────────────────────────────────────

OPERATIONAL_SENTENCE_PATTERNS = [
    re.compile(r"입장료"),
    re.compile(r"\d[\d,]*\s*원\s*\(?\s*(성인|어린이|청소년|경로)"),
    re.compile(r"www\.[a-zA-Z0-9./_-]+"),
    re.compile(r"https?://"),
    re.compile(r"[a-zA-Z0-9][a-zA-Z0-9.-]*\.(com|co\.kr|kr|net|org)\b"),
    re.compile(r"\d{2,4}[-.]\d{3,4}[-.]\d{4}"),  # 전화번호
    re.compile(r"패스포트\s*소지자"),
    re.compile(r"\d+\s*%\s*할인|할인\s*(혜택|가능)?"),
    re.compile(r"휴무일|운영시간|개관시간|영업시간"),
    re.compile(r"문의\s*[:：]"),
]


# 페이지 여백의 인쇄 쪽번호가 옆 줄 끝에 딸려와 문장 중간에 홀로 낀 잔재
# (예: "...따라 내려오도록 한다. 231 안내 화살표를..."). 이 가이드북의 코스 3~21은
# 인쇄 쪽번호 약 58~282 범위에 걸쳐 있으므로, 그 범위의 고립된 2~3자리 숫자만
# 제거한다(연도 "2002", "7000년", 코스 번호 "20 코스" 등은 범위 밖이라 보존됨).
STRAY_PAGE_NUMBER_RANGE = (55, 300)
STRAY_PAGE_NUMBER_RE = re.compile(r"(?<=\s)(\d{2,3})(?=\s)")


def strip_stray_page_numbers(text: str) -> str:
    lo, hi = STRAY_PAGE_NUMBER_RANGE

    def _repl(m: re.Match) -> str:
        n = int(m.group(1))
        return "" if lo <= n <= hi else m.group(0)

    result = STRAY_PAGE_NUMBER_RE.sub(_repl, text)
    return re.sub(r"[ \t]{2,}", " ", result)


# 페이지 모서리의 세로 회전 코스 코드(예: "14-1")가 회전 텍스트 추출 시 순서가
# 뒤집혀 "1-41" 형태로 본문 끝에 딸려오는 잔재. 워터마크 "etuoR ellO ujeJ"와
# 같은 원인(세로/회전 텍스트의 문자 순서 역전)이다.
STRAY_COURSE_CODE_RE = re.compile(r"(?<=\s)\d{1,2}-\d{1,2}(?=\s|$)")


def strip_stray_course_codes(text: str) -> str:
    result = STRAY_COURSE_CODE_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", result)


# ────────────────────────────────────────────────
# 줄바꿈 결합 시 부자연스러운 공백 방지
# ────────────────────────────────────────────────
# 이 가이드북은 좁은 컬럼 폭에 맞춰 줄을 바꾸는데, 대부분은 어절(단어) 경계에서
# 바뀌지만("보여주" / "는 길이다") 종종 한 단어 중간에서 강제로 끊기기도 한다
# ("모여들었" / "다.", "북" / "쪽은"). 원래 공백이 있었는지 여부는 텍스트만으로는
# 완전히 판별할 수 없으므로(형태소 분석기 없이는), 기본값은 "공백 유지"로 두고
# — 조사/어미처럼 앞말에 절대 공백 없이 붙는 것이 확실한 토큰으로 다음 줄이
# 시작할 때만 공백을 생략한다. 이 방식이 지나치게 공격적으로 공백을 없애 새로운
# 오류를 만드는 것(예: "만날 수 있다" → "만날수있다")보다 안전하다는 것을
# 검수를 통해 확인했다.
NO_SPACE_CONTINUATION_TOKENS = {
    "을", "를", "은", "는", "의", "로", "와", "과", "만", "도",
    "다", "요", "죠", "네요", "습니다", "니다", "는데", "은데",
    "겠다", "겠죠", "겠네", "겠네요", "다가", "다면", "라면", "려면",
    "고서", "면서", "지만", "라도", "든지", "거든", "잖아", "잖아요",
    "이다", "한다", "했다", "된다",
}
TRAILING_PUNCT_RE = re.compile(r"^([가-힣]+)([.,!?:;·]*)$")


def _bare_first_token(line: str) -> str | None:
    m = re.match(r"^(\S+)", line.strip())
    if not m:
        return None
    m2 = TRAILING_PUNCT_RE.match(m.group(1))
    return m2.group(1) if m2 else m.group(1)


def join_wrapped_lines(paragraph: str) -> str:
    """줄바꿈으로 잘린 줄들을 자연스러운 문장으로 결합합니다."""
    lines = paragraph.split("\n")
    result = lines[0].rstrip() if lines else ""
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            result += "\n"  # 원래 있던 빈 줄(문단 구분)은 유지
            continue
        if _bare_first_token(stripped) in NO_SPACE_CONTINUATION_TOKENS:
            result = result.rstrip() + stripped  # 조사/어미 등은 공백 없이 바로 붙인다
        else:
            result = result.rstrip() + " " + stripped
    return result


def split_sentences(paragraph: str) -> list[str]:
    """줄바꿈을 자연스럽게 결합한 뒤 문장 단위로 분리합니다."""
    flat = join_wrapped_lines(paragraph).strip()
    flat = re.sub(r"[ \t]{2,}", " ", flat)
    if not flat:
        return []
    return re.split(r"(?<=[.!?])\s+", flat)


def strip_operational_info(text: str) -> str:
    """컬럼(문단) 블록 단위로 운영정보(입장료/연락처/URL 등) 문장만 제거합니다."""
    blocks = text.split("\n\n")
    cleaned_blocks = []
    for block in blocks:
        sentences = split_sentences(block)
        kept = [
            s
            for s in sentences
            if not any(pat.search(s) for pat in OPERATIONAL_SENTENCE_PATTERNS)
        ]
        if kept:
            cleaned_blocks.append(" ".join(kept))
    return "\n\n".join(cleaned_blocks)


# ────────────────────────────────────────────────
# A/B 분기 코스 분리 (3코스 → 3-A/3-B, 15코스 → 15-A/15-B)
# ────────────────────────────────────────────────

AB_SPLIT_COURSES = {
    "3코스": ("3-A코스", "3-B코스"),
    "15코스": ("15-A코스", "15-B코스"),
}
B_MARKER_RE = re.compile(r"B\s*코스\s*는")
REJOIN_RE = re.compile(r"A\s*와\s*B\s*코스가?\s*(?:만나|합쳐|합류)")


def split_ab_text(text: str) -> tuple[str, str] | None:
    """공용 구간(A) 텍스트 안에서 B코스 전용 서술이 시작되는 지점을 문장 단위로 찾아 분리합니다."""
    blocks = text.split("\n\n")
    all_sentences: list[tuple[int, str]] = []
    for bi, block in enumerate(blocks):
        for s in re.split(r"(?<=[.!?])\s+", block.strip()):
            s = s.strip()
            if s:
                all_sentences.append((bi, s))

    b_idx = next(
        (i for i, (_, s) in enumerate(all_sentences) if B_MARKER_RE.search(s)), None
    )
    if b_idx is None:
        return None

    rejoin_idx = next(
        (
            i
            for i in range(b_idx + 1, len(all_sentences))
            if REJOIN_RE.search(all_sentences[i][1])
        ),
        None,
    )

    a_sentences = all_sentences[:b_idx]
    if rejoin_idx is not None:
        a_sentences = a_sentences + all_sentences[rejoin_idx:]
    b_sentences = all_sentences[b_idx:]

    def rebuild(sentence_list: list[tuple[int, str]]) -> str:
        out_blocks: list[str] = []
        cur_block = None
        cur: list[str] = []
        for bi, s in sentence_list:
            if cur_block is None or bi != cur_block:
                if cur:
                    out_blocks.append(" ".join(cur))
                cur = [s]
                cur_block = bi
            else:
                cur.append(s)
        if cur:
            out_blocks.append(" ".join(cur))
        return "\n\n".join(out_blocks)

    return rebuild(a_sentences), rebuild(b_sentences)


def is_remove_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False

    for pat in LINE_EXACT_REMOVE_PATTERNS:
        if pat.match(stripped):
            return True

    for keyword in LINE_CONTAINS_REMOVE:
        if keyword in stripped:
            return True

    if SIDEBAR_TITLE_RE.match(stripped):
        return True

    return False


def is_sidebar_block_start(line: str) -> bool:
    stripped = line.strip()
    for pat in SIDEBAR_BLOCK_START_PATTERNS:
        if pat.search(stripped):
            return True
    return False


def filter_sidebar_blocks(lines: list[str]) -> list[str]:
    result = []
    in_sidebar = False
    sidebar_count = 0
    MAX_SIDEBAR_LINES = 8

    for line in lines:
        stripped = line.strip()

        if in_sidebar:
            sidebar_count += 1
            if not stripped or sidebar_count > MAX_SIDEBAR_LINES:
                in_sidebar = False
                sidebar_count = 0
                if stripped:
                    result.append(line)
            continue

        if is_sidebar_block_start(line):
            in_sidebar = True
            sidebar_count = 0
            continue

        result.append(line)

    return result


def clean_text(text: str) -> str:
    lines = text.splitlines()
    step1 = [line for line in lines if not is_remove_line(line)]
    step2 = filter_sidebar_blocks(step1)
    joined = "\n".join(step2)
    result = re.sub(r"\n{3,}", "\n\n", joined)
    return result.strip()


# ────────────────────────────────────────────────
# 컬럼(좌/우) 분리 PDF 추출
# ────────────────────────────────────────────────


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    lines = []
    cur_top = None
    cur: list[dict] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        t = round(w["top"])
        if cur_top is None or abs(t - cur_top) <= 2:
            cur.append(w)
            cur_top = t if cur_top is None else cur_top
        else:
            lines.append(cur)
            cur = [w]
            cur_top = t
    if cur:
        lines.append(cur)
    return lines


def _header_word_set(crop) -> set:
    """본문보다 폰트 크기가 뚜렷이 큰(러닝헤더/박스제목) 단어 집합을 반환합니다."""
    words = crop.extract_words(extra_attrs=["size"])
    if not words:
        return set()

    header_words = set()
    for line_words in _group_words_into_lines(words):
        avg_size = sum(w["size"] for w in line_words) / len(line_words)
        if avg_size > HEADER_FONT_SIZE_THRESHOLD:
            for w in line_words:
                header_words.add(w["text"])
    return header_words


def _boxes_close(a, b, gap: float) -> bool:
    ax0, at, ax1, ab = a
    bx0, bt, bx1, bb = b
    return not (ax1 + gap < bx0 or bx1 + gap < ax0 or ab + gap < bt or bb + gap < at)


def _union_box(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def get_box_regions(page) -> list[tuple[float, float, float, float]]:
    """페이지에서 말풍선/박스형 스페셜 정보의 그래픽(둥근 테두리 곡선) 영역을 탐지합니다."""
    curves = page.curves
    if not curves:
        return []

    # 페이지 우측 끝의 세로 장식 바("Jeju Olle Route")는 박스와 무관하므로 제외
    margin_threshold = page.width - SIDEBAR_MARGIN_WIDTH
    boxes = [
        [c["x0"], c["top"], c["x1"], c["bottom"]]
        for c in curves
        if c["x0"] < margin_threshold
    ]
    if not boxes:
        return []

    changed = True
    while changed:
        changed = False
        merged: list[list[float]] = []
        used = [False] * len(boxes)
        for i in range(len(boxes)):
            if used[i]:
                continue
            cur = boxes[i]
            used[i] = True
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                if _boxes_close(cur, boxes[j], BOX_MERGE_GAP):
                    cur = _union_box(cur, boxes[j])
                    used[j] = True
                    changed = True
            merged.append(cur)
        boxes = merged

    result = []
    for x0, top, x1, bottom in boxes:
        if (x1 - x0) * (bottom - top) >= BOX_MIN_AREA:
            result.append((x0, top, x1, bottom))
    return result


def clean_column_text(crop) -> str:
    """컬럼 텍스트에서 제목성 폰트(러닝헤더/박스제목) 라인을 제거합니다."""
    text = crop.extract_text() or ""
    if not text:
        return ""

    badge_m = LATIN_BADGE_RE.search(text)
    if badge_m:
        # 학습 코너 장식 배지 발견 — 이 컬럼의 나머지는 코너 박스이므로 전부 버린다.
        text = text[: badge_m.start()]
        if not text.strip():
            return ""

    header_words = _header_word_set(crop)

    out_lines = []
    for line in text.split("\n"):
        tokens = line.split()
        if not tokens:
            out_lines.append(line)
            continue
        hit = sum(1 for tok in tokens if tok in header_words)
        if hit / len(tokens) >= HEADER_LINE_WORD_RATIO:
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _exclude_box_regions(crop, regions, x_start: float, x_end: float, height: float):
    """주어진 컬럼(x_start~x_end) 범위와 겹치는 박스 영역만 잘라 적용합니다."""
    for x0, top, x1, bottom in regions:
        cx0 = max(x0, x_start)
        cx1 = min(x1, x_end)
        if cx1 <= cx0:
            continue  # 이 컬럼과 가로로 겹치지 않음
        cy0 = max(top, 0)
        cy1 = min(bottom, height)
        if cy1 <= cy0:
            continue
        crop = crop.outside_bbox((cx0, cy0, cx1, cy1))
    return crop


MIN_AD_IMAGE_AREA = 3000  # 이 면적(pt^2) 이상의 래스터 이미지가 있으면 광고/보너스 페이지로 간주


def _has_ad_image(crop) -> bool:
    """실사 이미지가 삽입되어 있으면 스폰서 광고/보너스 테마 페이지로 판단합니다.

    이 가이드북의 일반 코스 본문 페이지는 선묘 지도/그래픽만 사용하고 래스터
    사진을 넣지 않는다. 반면 '이니스프리 제주하우스' 같은 협찬 광고나
    'Bonus Page' 특집 페이지는 실사 사진을 포함한 전면 스프레드로 삽입되어
    있어, 컬럼 분리만으로는 본문과 구분되지 않는다.
    """
    for im in crop.images:
        area = (im["x1"] - im["x0"]) * (im["bottom"] - im["top"])
        if area >= MIN_AD_IMAGE_AREA:
            return True
    return False


def extract_column_pair(page) -> tuple[str, str]:
    """페이지를 좌/우로 분리하고 말풍선/박스형 스페셜 정보 영역을 제외한 뒤 텍스트를 반환합니다."""
    w, h = page.width, page.height
    mid = w / 2
    regions = get_box_regions(page)

    left_crop = page.crop((0, 0, mid, h))
    right_crop = page.crop((mid, 0, w, h))

    if _has_ad_image(left_crop):
        left = ""
    else:
        left = clean_column_text(_exclude_box_regions(left_crop, regions, 0, mid, h))

    if _has_ad_image(right_crop):
        right = ""
    else:
        right = clean_column_text(_exclude_box_regions(right_crop, regions, mid, w, h))

    return left, right


def detect_course_spans(pages: list[dict]) -> list[dict]:
    """parser.parse_courses_structured 와 동일한 로직으로 코스 시작 페이지를 감지합니다."""
    course_spans = []
    for i, pg in enumerate(pages):
        text = pg["text"]
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            continue

        top_lines = "\n".join(lines[:5])
        is_first_page = ("Jeju Olle Route" in top_lines) or (
            "개장 :" in text and "스탬프 찍는 곳" in text
        )

        if is_first_page:
            c_name = extract_course_name_from_first_page(lines, text)
            if c_name and c_name != "Jeju Olle Route":
                course_spans.append({"course_name": c_name, "start_idx": i})

    return course_spans


def run():
    print(f"[*] PDF 컬럼 분리 추출 시작 ({START_PAGE}~{END_PAGE} 페이지): {PDF_PATH}")

    if not os.path.exists(PDF_PATH):
        print(f"[ERROR] PDF 파일을 찾을 수 없습니다: {PDF_PATH}")
        sys.exit(1)

    results = []

    with pdfplumber.open(PDF_PATH) as pdf:
        total_pages = len(pdf.pages)
        last_page = min(END_PAGE, total_pages)

        pages = []
        for idx in range(START_PAGE - 1, last_page):
            p = pdf.pages[idx]
            text = p.extract_text() or ""
            pages.append({"page": p, "text": text, "page_num": idx + 1})

        print(f"[+] 페이지 로드 완료: {len(pages)}개")

        course_spans = detect_course_spans(pages)
        print(f"[*] 총 {len(course_spans)}개 코스 섹션 발견")

        num = len(course_spans)
        for idx, span in enumerate(course_spans):
            c_name = span["course_name"]
            start = span["start_idx"]
            end = course_spans[idx + 1]["start_idx"] if (idx + 1) < num else len(pages)

            detail_indices = range(start + 2, end)  # 0:간단정보, 1:지도, 2+:상세 본문

            raw_parts = []
            for pi in detail_indices:
                left, right = extract_column_pair(pages[pi]["page"])
                if left.strip():
                    raw_parts.append(left)
                if right.strip():
                    raw_parts.append(right)

            raw_detail = "\n\n".join(raw_parts)
            cleaned = strip_stray_course_codes(
                strip_stray_page_numbers(strip_operational_info(clean_text(raw_detail)))
            )

            if c_name in AB_SPLIT_COURSES:
                split_result = split_ab_text(cleaned)
                if split_result:
                    name_a, name_b = AB_SPLIT_COURSES[c_name]
                    text_a, text_b = split_result
                    for name, text in ((name_a, text_a), (name_b, text_b)):
                        results.append(
                            {
                                "course_name": name,
                                "title": f"{name} 상세 코스 정보",
                                "detail_text": text,
                                "char_count": len(text),
                            }
                        )
                        print(f"  [+] {name}: {len(text)}자 (A/B 분리)")
                    continue
                print(f"  [!] {c_name}: B코스 분기점을 찾지 못해 분리하지 못했습니다.")

            results.append(
                {
                    "course_name": c_name,
                    "title": f"{c_name} 상세 코스 정보",
                    "detail_text": cleaned,
                    "char_count": len(cleaned),
                }
            )
            print(f"  [+] {c_name}: {len(raw_detail)}자 → {len(cleaned)}자 (노이즈 제거 후)")

    save_json(results, OUTPUT_JSON)
    save_csv(results, OUTPUT_CSV)
    print_summary(results)

    print("\n[OK] 코스 상세 텍스트 추출 완료!")
    print(f"     임베딩 입력 파일: {OUTPUT_JSON}")
    print(f"     검수용 CSV 파일:  {OUTPUT_CSV}")


def save_json(results: list[dict], output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_data = [
        {"course_name": r["course_name"], "title": r["title"], "detail_text": r["detail_text"]}
        for r in results
    ]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"\n[+] JSON 저장 완료: {output_path} ({len(save_data)}개 코스)")


def save_csv(results: list[dict], output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["course_name", "title", "char_count", "detail_text"])
        for r in results:
            writer.writerow([r["course_name"], r["title"], r["char_count"], r["detail_text"]])
    print(f"[+] CSV 저장 완료: {output_path} ({len(results)}개 코스)")


def print_summary(results: list[dict]) -> None:
    total = len(results)
    filled = sum(1 for r in results if r["char_count"] > 0)
    avg_chars = sum(r["char_count"] for r in results) / total if total > 0 else 0
    print(f"\n{'='*50}")
    print("[ 추출 결과 요약 ]")
    print(f"  총 코스 수       : {total}개")
    print(f"  텍스트 추출 성공 : {filled}개")
    print(f"  평균 텍스트 길이 : {avg_chars:.0f}자")
    print(f"  총 텍스트 길이   : {sum(r['char_count'] for r in results)}자")

    empty = [r for r in results if r["char_count"] == 0]
    if empty:
        print("\n  [!] 텍스트 없는 코스:")
        for r in empty:
            print(f"      - {r['course_name']}")
    print("=" * 50)


if __name__ == "__main__":
    run()
