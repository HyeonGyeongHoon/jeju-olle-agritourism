from unittest.mock import MagicMock, patch

from src.agent import nodes
from src.agent.nodes import _search_culture_knowledge


def _rpc_client(rows, known_crops=()):
    """match_culture_chunks RPC 결과(rows)와 courses.crops 목록(known_crops)을 함께 목킹합니다.
    known_crops 는 _get_known_crop_tags 가 "key_item_or_crop 이 실제 작물명인가"를 판정하는 데
    쓰입니다."""
    client = MagicMock()
    client.rpc.return_value.execute.return_value.data = rows

    courses_table = MagicMock()
    courses_table.select.return_value.execute.return_value.data = (
        [{"crops": ",".join(known_crops)}] if known_crops else []
    )
    client.table.side_effect = lambda name: courses_table if name == "courses" else MagicMock()
    return client


def test_filters_out_documents_tagged_with_a_different_crop():
    """match_culture_chunks RPC 는 작물 하드 필터가 없는 순수 유사도 검색이라, "당근" 질의에
    "마늘" 문서가 섞여 나올 수 있다 — 이런 다른 작물 문서는 걸러져야 한다."""
    client = _rpc_client(
        [{"id": 1, "crop_name": "마늘", "title": "마늘 재배", "content": "...", "similarity": 0.9,
          "target_crop": "마늘"}],
        known_crops=["당근", "마늘"],
    )

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_local_culture_docs", return_value=[]) as mock_local:
        result = _search_culture_knowledge(client, "당근", "당근")

    assert result == []
    mock_local.assert_called_once()


def test_keeps_documents_matching_the_requested_crop():
    client = _rpc_client(
        [{"id": 1, "crop_name": "당근", "title": "당근 재배", "content": "...", "similarity": 0.9,
          "target_crop": "당근"}],
        known_crops=["당근"],
    )

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]):
        result = _search_culture_knowledge(client, "당근", "당근")

    assert len(result) == 1
    assert result[0]["title"] == "당근 재배"


def test_excludes_general_documents_when_the_requested_item_is_a_real_crop():
    """"당근"처럼 실제 작물명을 물어봤는데 당근 문서가 없다면, crop_name 없는 일반 문화 문서
    (밭담 등)로 슬쩍 대체하지 않고 아무 것도 없다고 정직하게 처리해야 한다(꾸며내기 금지)."""
    client = _rpc_client(
        [
            {"id": 1, "crop_name": None, "title": "제주 밭담 문화", "content": "...", "similarity": 0.8,
             "target_crop": None},
            {"id": 2, "crop_name": "마늘", "title": "마늘 재배", "content": "...", "similarity": 0.9,
             "target_crop": "마늘"},
        ],
        known_crops=["당근", "마늘"],
    )

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_local_culture_docs", return_value=[]) as mock_local:
        result = _search_culture_knowledge(client, "당근", "당근")

    assert result == []
    # 로컬 폴백도 무관한 일반 문서로 채우지 말라고 지시해야 한다.
    mock_local.assert_called_once_with("당근", "당근", allow_general_fallback=False)


def test_keeps_general_documents_when_the_requested_item_is_a_non_crop_theme():
    """key_item_or_crop 이 "밭담"처럼 실제 작물이 아닌 테마어라면, crop_name 없는 일반 문화
    문서가 바로 정답일 수 있으므로 그대로 유지되어야 한다."""
    client = _rpc_client(
        [
            {"id": 1, "crop_name": None, "title": "제주 밭담 문화", "content": "...", "similarity": 0.8,
             "target_crop": None},
            {"id": 2, "crop_name": "마늘", "title": "마늘 재배", "content": "...", "similarity": 0.9,
             "target_crop": "마늘"},
        ],
        known_crops=["마늘"],  # "밭담"은 known_crops 에 없음 -> 작물이 아닌 테마어로 판정됨
    )

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]):
        result = _search_culture_knowledge(client, "밭담", "밭담")

    titles = [r["title"] for r in result]
    assert "제주 밭담 문화" in titles
    assert "마늘 재배" not in titles


def test_no_filtering_applied_when_key_item_or_crop_is_absent():
    client = _rpc_client([
        {"id": 1, "crop_name": "마늘", "title": "마늘 재배", "content": "...", "similarity": 0.9,
         "target_crop": "마늘"},
        {"id": 2, "crop_name": "감귤", "title": "감귤 재배", "content": "...", "similarity": 0.85,
         "target_crop": "감귤"},
    ])

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]):
        result = _search_culture_knowledge(client, None, "제주 작물 이야기")

    assert len(result) == 2


def test_falls_back_to_local_docs_when_all_rpc_results_are_wrong_crop():
    client = _rpc_client(
        [{"id": 1, "crop_name": "마늘", "title": "마늘 재배", "content": "...", "similarity": 0.9,
          "target_crop": "마늘"}],
        known_crops=["당근", "마늘"],
    )
    local_fallback_result = [{"title": "당근 재배(로컬)", "crop_name": "당근"}]

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_local_culture_docs", return_value=local_fallback_result) as mock_local:
        result = _search_culture_knowledge(client, "당근", "당근")

    assert result == local_fallback_result
    mock_local.assert_called_once_with("당근", "당근", allow_general_fallback=False)


def test_falls_back_to_local_docs_when_rpc_call_itself_raises(monkeypatch):
    """QA 시나리오 F1: culture_crop_knowledge RPC 조회 자체가 실패해도(DB 연결 문제 등),
    예외를 삼키고 로컬 JSON 문서 검색으로 폴백해야 합니다 — 크롭 불일치로 결과가 빈 경우와는
    다른 코드 경로(바깥 try/except)라 별도로 검증합니다."""
    client = MagicMock()
    client.rpc.side_effect = Exception("connection refused")
    local_fallback_result = [{"title": "당근 재배(로컬)", "crop_name": "당근"}]

    with patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_local_culture_docs", return_value=local_fallback_result) as mock_local:
        result = _search_culture_knowledge(client, "당근", "당근")

    assert result == local_fallback_result
    mock_local.assert_called_once()
