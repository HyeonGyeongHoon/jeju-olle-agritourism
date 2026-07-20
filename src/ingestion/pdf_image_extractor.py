"""
각 올레길 코스의 지도 페이지(2번째 페이지)를 PNG 이미지로 추출하는 모듈입니다.
PyMuPDF(fitz)를 사용하여 고해상도 이미지를 추출합니다.
"""

import os

import fitz  # PyMuPDF


def extract_map_page_as_image(
    pdf_path: str,
    page_num: int,
    output_dir: str,
    dpi: int = 150,
) -> str:
    """PDF의 특정 페이지를 PNG 이미지로 추출합니다.

    Args:
        pdf_path: PDF 파일 경로
        page_num: 추출할 페이지 번호 (1-indexed)
        output_dir: 이미지 저장 디렉토리
        dpi: 이미지 해상도 (기본값: 150)

    Returns:
        저장된 이미지 파일 경로
    """
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]  # 0-indexed

    zoom = dpi / 72  # 기본 DPI 72 → 목표 DPI 변환 비율
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    output_path = os.path.join(output_dir, f"page_{page_num:03d}.png")
    pix.save(output_path)
    doc.close()

    return output_path


def extract_course_map_images(
    pdf_path: str,
    course_page_map: dict[str, int],
    output_dir: str = "data/map_images",
    dpi: int = 150,
) -> dict[str, str]:
    """각 코스의 지도 페이지(2번째 페이지)를 PNG 이미지로 일괄 추출합니다.

    Args:
        pdf_path: PDF 파일 경로
        course_page_map: {코스명: 지도 페이지 번호} 딕셔너리
        output_dir: 이미지 저장 디렉토리
        dpi: 이미지 해상도

    Returns:
        {코스명: 이미지 파일 경로} 딕셔너리
    """
    result = {}
    for course_name, page_num in course_page_map.items():
        safe_name = course_name.replace("/", "-").replace(" ", "_")
        out_path = os.path.join(output_dir, f"{safe_name}_map_page_{page_num:03d}.png")
        os.makedirs(output_dir, exist_ok=True)

        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        pix.save(out_path)
        doc.close()

        result[course_name] = out_path

    return result
