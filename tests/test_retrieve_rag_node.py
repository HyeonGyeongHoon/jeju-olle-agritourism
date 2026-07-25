from types import SimpleNamespace
from unittest.mock import patch

from src.agent import nodes
from src.agent.nodes import retrieve_rag_node


class _FakeCoursesTable:
    """courses 테이블에 대한 두 가지 조회 패턴(select("id") 로 하는 RDB 필터링,
    select("*").eq("id", X) 로 하는 청크별 메타데이터 조회)을 구분해서 응답하는 가짜 테이블."""

    def __init__(self, rdb_ids, course_meta_by_id, raise_for_id=None):
        self._rdb_ids = rdb_ids
        self._course_meta_by_id = course_meta_by_id
        self._raise_for_id = raise_for_id
        self._mode = None
        self._eq_id = None

    def select(self, cols):
        self._mode = "rdb" if cols == "id" else "meta"
        return self

    def eq(self, col, value):
        if col == "id":
            self._eq_id = value
        return self

    def execute(self):
        if self._mode == "rdb":
            return SimpleNamespace(data=[{"id": i} for i in self._rdb_ids])
        if self._raise_for_id is not None and self._eq_id == self._raise_for_id:
            raise Exception("DB 결측치로 인한 예외 (테스트)")
        meta = self._course_meta_by_id.get(self._eq_id)
        return SimpleNamespace(data=[meta] if meta else [])


class _FakeCoursesTableForTargetCourseHardConstraint:
    """RDB 하드 필터(select "id"), target_course 완전 일치 필터(select
    "id,administrative_areas,course_name" + in_), _describe_target_course_mismatch 조회(select
    "course_name,has_wheelchair_segment" + eq) 세 가지 조회 패턴을 select 컬럼 문자열로 구분해
    응답하는 가짜 테이블."""

    def __init__(self, rdb_ids, course_rows_by_id, wheelchair_status_by_course_name):
        self._rdb_ids = rdb_ids
        self._course_rows_by_id = course_rows_by_id
        self._wheelchair_status_by_course_name = wheelchair_status_by_course_name
        self._select_cols = None
        self._eq_course_name = None
        self._in_ids = None

    def select(self, cols):
        self._select_cols = cols
        return self

    def eq(self, col, value):
        if col == "course_name":
            self._eq_course_name = value
        return self

    def in_(self, col, values):
        self._in_ids = values
        return self

    def execute(self):
        if self._select_cols == "id":
            return SimpleNamespace(data=[{"id": i} for i in self._rdb_ids])
        if self._select_cols == "course_name,has_wheelchair_segment":
            status = self._wheelchair_status_by_course_name.get(self._eq_course_name)
            if status is None:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{"course_name": self._eq_course_name, "has_wheelchair_segment": status}])
        if self._select_cols == "id,administrative_areas,course_name":
            rows = [
                {**self._course_rows_by_id[i], "id": i}
                for i in (self._in_ids or [])
                if i in self._course_rows_by_id
            ]
            return SimpleNamespace(data=rows)
        return SimpleNamespace(data=[])


class _FakeOtherTable:
    """course_sub_segments 등 이 테스트에서 신경 쓰지 않는 테이블용 관대한 스텁."""

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _FakeClient:
    def __init__(self, courses_table, rpc_data):
        self._courses_table = courses_table
        self._rpc_data = rpc_data

    def table(self, name):
        if name == "courses":
            return self._courses_table
        return _FakeOtherTable()

    def rpc(self, name, params):
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self._rpc_data))


def _base_state():
    return {
        "parsed_constraints": {"hard_constraints": {}, "vector_query": "테스트 질의"},
        "safety_check": {},
        "target_course": None,
        "b2b_params": {
            "key_item_or_crop": None,
            "preferred_location": None,
            "target_month": None,
            "include_market_insights": False,
        },
        "query": "테스트 질의",
    }


def test_retrieve_rag_node_isolates_one_bad_chunk_from_the_rest():
    """회귀 방지: 청크 하나의 조립(코스 메타데이터 조회)이 실패해도(예: DB 결측치로 인한
    예외), 그 청크만 건너뛰고 나머지 정상 청크는 그대로 살아남아야 합니다 — 예전엔 이 실패가
    바깥 try 안에 있어서 그 시점 이후의 모든 청크가 통째로 버려졌습니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1, 2],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": "감귤", "administrative_areas": "성산읍",
                "total_distance_km": 15.0, "estimated_time_text": "5시간", "difficulty": "중",
            },
        },
        raise_for_id=2,
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[
            {"id": 10, "course_id": 1, "title": "정상 청크", "content": "...", "similarity": 0.9},
            {"id": 11, "course_id": 2, "title": "문제 청크", "content": "...", "similarity": 0.8},
        ],
    )

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(_base_state())

    chunks = result["retrieved_chunks"]
    assert len(chunks) == 1
    assert chunks[0]["course_id"] == 1
    assert chunks[0]["course_name"] == "1코스"


def test_retrieve_rag_node_treats_null_distance_as_zero_instead_of_crashing():
    """total_distance_km 컬럼 값이 NULL(None)이어도(키 자체는 존재) 청크 조립이 죽지 않고
    0.0 으로 안전하게 처리되어야 합니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": "감귤", "administrative_areas": "성산읍",
                "total_distance_km": None, "estimated_time_text": "5시간", "difficulty": "중",
            },
        },
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[
            {"id": 10, "course_id": 1, "title": "청크", "content": "...", "similarity": 0.9},
        ],
    )

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(_base_state())

    chunks = result["retrieved_chunks"]
    assert len(chunks) == 1
    assert chunks[0]["total_distance_km"] == 0.0


def test_retrieve_rag_node_stops_without_substitution_when_target_course_fails_hard_constraint():
    """사용자 요청: "휠체어가 필요한 2코스 기획서 만들어줘"처럼 지정한 코스가 하드 제약(휠체어)을
    만족 못 하면, 다른 코스로 조용히 대체 추천하지 말고 chunks=[] 와 함께 구체적인 사유만 남겨야
    합니다(대체 추천 없이 종료 — report_generator 가 이 사유를 그대로 최종 답변에 씀).
    벡터 검색(임베딩 호출)까지 갈 필요도 없어야 합니다."""
    courses_table = _FakeCoursesTableForTargetCourseHardConstraint(
        rdb_ids=[10],
        course_rows_by_id={10: {"course_name": "12코스", "administrative_areas": "무릉리"}},
        wheelchair_status_by_course_name={"2코스": "없음"},
    )
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["target_course"] = "2코스"
    state["parsed_constraints"] = {"hard_constraints": {"wheelchair_required": True}, "vector_query": "휠체어 2코스"}

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["retrieved_chunks"] == []
    assert result["fallback_applied"] is True
    assert "2코스" in result["fallback_reason"]
    assert "휠체어" in result["fallback_reason"]
    mock_embed.assert_not_called()


def test_retrieve_rag_node_ignores_course_name_given_as_preferred_location():
    """회귀 방지: intent_parser 는 "1코스로 기획서 만들어줘"에서 preferred_location='1코스'
    (지역이 아니라 코스명)를 채웁니다. 이 값을 지역 조건으로 쓰면 (1) 겹치는 지역이 0건이라
    "'1코스' 지역과 겹치는 코스를 찾지 못했다"는 엉뚱한 각주가 붙고, (2) 부분 일치인
    _crop_location_boost 가 "1코스"를 "10-1코스"의 course_name 에 매칭시켜 전혀 다른 코스를
    상위로 끌어올립니다. 코스 지목은 target_course 필터가 처리하므로 지역 조건으로는 무시해야
    합니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": "감귤", "administrative_areas": "시흥리",
                "total_distance_km": 15.1, "estimated_time_text": "5시간", "difficulty": "중",
            },
        },
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[{"id": 10, "course_id": 1, "title": "1코스 청크", "content": "...", "similarity": 0.9}],
    )

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": None,
        "preferred_location": "1코스",
        "target_month": 7,
        "include_market_insights": True,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_filter_course_ids_by_location") as mock_location_filter, \
         patch.object(nodes, "_fetch_market_insight", return_value=None) as mock_fetch_market, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    mock_location_filter.assert_not_called()
    assert mock_fetch_market.call_args[0][1] is None
    assert result["fallback_applied"] is False
    assert result["fallback_reason"] is None


def test_retrieve_rag_node_still_applies_real_region_as_location_filter():
    """대조군: preferred_location 이 실제 지역명이면 지역 하드 필터는 그대로 동작해야 합니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": "감귤", "administrative_areas": "시흥리",
                "total_distance_km": 15.1, "estimated_time_text": "5시간", "difficulty": "중",
            },
        },
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[{"id": 10, "course_id": 1, "title": "1코스 청크", "content": "...", "similarity": 0.9}],
    )

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": None,
        "preferred_location": "성산읍",
        "target_month": 7,
        "include_market_insights": False,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_filter_course_ids_by_location", return_value=([1], True)) as mock_location_filter, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        retrieve_rag_node(state)

    mock_location_filter.assert_called_once()
    assert mock_location_filter.call_args[0][2] == "성산읍"
