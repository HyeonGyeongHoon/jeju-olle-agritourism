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
