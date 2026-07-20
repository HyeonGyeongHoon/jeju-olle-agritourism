import os

import pdfplumber


def extract_text_from_pdf(
    pdf_path: str,
    start_page: int = 1,
    end_page: int = None,
    exclude_pages: list[int] = None,
) -> str:
    """PDF 파일에서 텍스트를 추출하며 지정된 페이지 범위와 제외 페이지 목록을 적용합니다."""
    if exclude_pages is None:
        exclude_pages = []

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    extracted_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        last_page = end_page if end_page is not None else total_pages

        for idx in range(start_page, last_page + 1):
            if idx in exclude_pages or idx < 1 or idx > total_pages:
                continue
            # pdf.pages는 0-indexed이므로 (idx - 1) 사용
            page = pdf.pages[idx - 1]
            text = page.extract_text()
            if text:
                extracted_pages.append(text)

    return "\n\n".join(extracted_pages)


def extract_pages_from_pdf(
    pdf_path: str,
    start_page: int = 1,
    end_page: int = None,
    exclude_pages: list[int] = None,
) -> list[dict]:
    """PDF 파일에서 페이지 번호와 해당 텍스트의 딕셔너리 리스트를 추출합니다."""
    if exclude_pages is None:
        exclude_pages = []

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    pages_data = []
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        last_page = end_page if end_page is not None else total_pages

        for idx in range(start_page, last_page + 1):
            if idx in exclude_pages or idx < 1 or idx > total_pages:
                continue
            page = pdf.pages[idx - 1]
            text = page.extract_text() or ""
            pages_data.append({"page_num": idx, "text": text.strip()})

    return pages_data
