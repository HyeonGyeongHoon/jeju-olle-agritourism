from src.agent.nodes import _search_local_culture_docs, _title_keywords


def test_title_keywords_strips_trailing_particles():
    assert "화산회토" in _title_keywords("화산회토와 제주 밭농사")
    assert "곶자왈" in _title_keywords("곶자왈과 중산간 개간사")
    assert "문화" not in _title_keywords("제주 밭담 문화 개론")


def test_search_local_culture_docs_matches_general_topic_by_keyword():
    """crop_name=None인 일반 문화 문서도 제목 키워드가 질의에 있으면 최우선으로 매칭되어야 한다
    (과거에는 파일에 등장하는 순서대로만 채워져 특정 일반 문화 질의가 무시되는 문제가 있었음)."""
    results = _search_local_culture_docs(None, "해녀들이 물질하는 바다 마을 이야기")
    assert results[0]["title"] == "해녀 물질과 '바다 밭' 문화"

    results = _search_local_culture_docs(None, "우영팟 텃밭에서 기르는 채소")
    assert results[0]["title"] == "우영팟(텃밭) 문화"

    results = _search_local_culture_docs(None, "말을 방목하는 목장과 촐 베기")
    assert results[0]["title"] == "방목과 촐(꼴) 문화"


def test_search_local_culture_docs_crop_match_still_prioritized():
    results = _search_local_culture_docs("감귤", "겨울 감귤 과수원 이야기")
    assert results[0]["crop_name"] == "감귤"


def test_search_local_culture_docs_falls_back_when_nothing_matches():
    """아무 키워드도 매칭되지 않아도 빈 리스트가 아니라 일반 문화 문서로 채워져야 한다."""
    results = _search_local_culture_docs(None, "휠체어로 갈 수 있는 코스 알려줘")
    assert len(results) == 3
    assert all(r["crop_name"] is None for r in results)
