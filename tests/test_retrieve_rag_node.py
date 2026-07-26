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
        # 무조건 반려(Fail-Fast) 정책 검증용: 벡터 검색 RPC(match_course_chunks)가 아예 호출되지
        # 않았음을 확인하기 위해 호출된 RPC 이름을 기록합니다.
        self.rpc_calls = []

    def table(self, name):
        if name == "courses":
            return self._courses_table
        return _FakeOtherTable()

    def rpc(self, name, params):
        self.rpc_calls.append(name)
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
    합니다. 2026-07-25 무조건 반려(Fail-Fast) 정책 전환 이후로는 이 사유를 fallback_reason 이
    아니라 exit_reason 에 담고 is_exit_early=True 를 세워, route_after_retriever 가
    report_generator 를 아예 건너뛰고 quick_responder 로 보내게 합니다(예전에는
    fallback_applied=True + fallback_reason 으로 report_generator 가 반려문을 썼음)."""
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
         patch.object(
             nodes, "_search_culture_knowledge", return_value=[]
         ) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["retrieved_chunks"] == []
    assert result["is_exit_early"] is True
    assert "2코스" in result["exit_reason"]
    assert "휠체어" in result["exit_reason"]
    # 완화(fallback)를 한 것이 아니라 반려한 것이므로 각주용 필드는 켜지지 않아야 합니다.
    assert result["fallback_applied"] is False
    assert result["fallback_reason"] is None
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []


def test_retrieve_rag_node_exits_early_when_target_course_name_does_not_exist():
    """정책 전환(2026-07-25 무조건 반려): target_course 가 DB의 어떤 course_name/
    administrative_areas 와도 겹치지 않으면(예: "가파도 코스"), 예전처럼 그 조건을 해제하고 전체
    코스에서 계속 검색(fallback_applied=True)하지 않고 즉시 반려해야 합니다. 벡터 검색 RPC와
    문화지식 검색이 한 번도 실행되지 않아야 합니다."""
    courses_table = _FakeCoursesTableForTargetCourseHardConstraint(
        rdb_ids=[10],
        course_rows_by_id={10: {"course_name": "12코스", "administrative_areas": "무릉리"}},
        wheelchair_status_by_course_name={},
    )
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["target_course"] = "가파도 코스"

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(
             nodes, "_search_culture_knowledge", return_value=[]
         ) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "가파도 코스" in result["exit_reason"]
    assert "생성할 수 없습니다" in result["exit_reason"]
    assert result["retrieved_chunks"] == []
    assert result["culture_chunks"] == []
    assert result["sub_segments"] == []
    assert result["fallback_applied"] is False
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []


def test_retrieve_rag_node_exits_early_when_no_course_overlaps_preferred_location():
    """정책 전환(2026-07-25 무조건 반려): 지역 필터가 0건이면 지역 조건을 풀고 전체 검색으로
    폴백하지 않고 즉시 반려해야 합니다. 예전 fail-soft 는 필터를 해제한 뒤 벡터/문화지식/세부구간
    조회를 모두 실행하고, 그 결과 quality_checker 가 반려와 무관한 컨텍스트로 최대 3회 재작성
    루프를 도는 낭비가 있었습니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[1, 2], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": "마늘",
        "preferred_location": "대정읍",
        "target_month": 3,
        "include_market_insights": False,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(
             nodes, "_filter_course_ids_by_location", return_value=([1, 2], False)
         ), \
         patch.object(nodes, "_filter_course_ids_by_crop") as mock_crop_filter, \
         patch.object(
             nodes, "_search_culture_knowledge", return_value=[]
         ) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "대정읍" in result["exit_reason"]
    assert "생성할 수 없습니다" in result["exit_reason"]
    assert result["retrieved_chunks"] == []
    assert result["fallback_applied"] is False
    assert result["fallback_reason"] is None
    # 지역 필터에서 이미 끊겼으므로 뒤따르는 작물 필터/벡터 검색/문화지식 검색은 실행 자체가
    # 되지 않아야 합니다(1~2초 내 결정론적 반려가 이 정책의 핵심).
    mock_crop_filter.assert_not_called()
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []


def test_retrieve_rag_node_exits_early_when_no_course_overlaps_crop():
    """정책 전환(2026-07-25 무조건 반려): 작물 필터가 0건이면(실제 작물명인데 겹치는 코스가 없음)
    작물 조건을 풀고 전체 검색으로 폴백하지 않고 즉시 반려해야 합니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[1, 2], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": "마늘",
        "preferred_location": None,
        "target_month": 3,
        "include_market_insights": False,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(
             nodes, "_filter_course_ids_by_crop", return_value=([1, 2], False)
         ), \
         patch.object(
             nodes, "_search_culture_knowledge", return_value=[]
         ) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "마늘" in result["exit_reason"]
    assert "생성할 수 없습니다" in result["exit_reason"]
    assert result["retrieved_chunks"] == []
    assert result["fallback_applied"] is False
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []


def test_retrieve_rag_node_marks_normal_path_as_not_exit_early():
    """대조군 겸 회귀 방지: 코스를 정상적으로 찾은 경로는 is_exit_early=False 를 명시적으로
    돌려줘야 합니다. 재작성 루프(query_rewriter → retriever)로 재진입했을 때 이전 순회에서 켜진
    플래그가 남아있으면, 이번엔 코스를 찾았는데도 route_after_retriever 가 반려로 보냅니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": "감귤",
                "administrative_areas": "시흥리", "total_distance_km": 15.1,
                "estimated_time_text": "5시간", "difficulty": "중",
            },
        },
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[{
            "id": 10, "course_id": 1, "title": "1코스 청크",
            "content": "...", "similarity": 0.9,
        }],
    )

    state = _base_state()
    state["is_exit_early"] = True  # 이전 순회에서 켜진 플래그
    state["exit_reason"] = "이전 순회의 반려 사유"

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["retrieved_chunks"]
    assert result["is_exit_early"] is False
    assert result["exit_reason"] is None


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

class _FakeCoursesTableForLocationCropCollision:
    """지역 필터(select _LOCATION_SELECT_COLS + in_), 작물 태그 조회(select "crops"),
    작물 필터(select "id,crops" + in_), RDB 하드 필터(select "id") 네 가지 조회 패턴을
    select 컬럼 문자열로 구분해 응답하는 가짜 테이블."""

    def __init__(self, rows):
        self._rows = rows
        self._select_cols = None
        self._in_ids = None

    def select(self, cols):
        self._select_cols = cols
        self._in_ids = None
        return self

    def in_(self, col, values):
        self._in_ids = values
        return self

    def execute(self):
        if self._select_cols == "id":
            return SimpleNamespace(data=[{"id": r["id"]} for r in self._rows])
        if self._select_cols == "crops":
            return SimpleNamespace(data=[{"crops": r["crops"]} for r in self._rows])
        selected = [r for r in self._rows if self._in_ids is None or r["id"] in self._in_ids]
        if self._select_cols == "id,crops":
            return SimpleNamespace(
                data=[{"id": r["id"], "crops": r["crops"]} for r in selected]
            )
        if self._select_cols == nodes._LOCATION_SELECT_COLS:
            return SimpleNamespace(
                data=[
                    {
                        "id": r["id"],
                        "administrative_areas": r["administrative_areas"],
                        "course_name": r["course_name"],
                        "eup_myeon_dong_areas": r["eup_myeon_dong_areas"],
                    }
                    for r in selected
                ]
            )
        return SimpleNamespace(data=[])


def test_retrieve_rag_node_does_not_match_other_eup_myeon_sharing_a_legal_ri_name():
    """회귀 방지(라이브 재현 2026-07-25 "구좌읍 감귤"): 예전엔 지역 필터가 "구좌읍"을 법정리
    후보로 역확장해 administrative_areas 에서 부분 문자열로 찾았기 때문에, "세화리"가 구좌읍과
    표선면 양쪽에 존재한다는 이유로 표선면/남원읍 코스인 4코스가 구좌읍 후보에 편입됐다. 그
    4코스는 crops 에 감귤이 있어 작물 필터까지 통과해, 구좌읍 실제 코스들이 감귤을 재배하지
    않는다는 사실에 근거한 무조건 반려를 우회하고 엉뚱한 지역의 기획서가 생성됐다. 이제는
    courses.eup_myeon_dong_areas 로 소속이 확정되므로 지역 필터가 구좌읍 코스만 남기고, 그
    코스에 감귤이 없으므로 작물 단계에서 정직하게 반려돼야 한다."""
    courses_table = _FakeCoursesTableForLocationCropCollision(
        rows=[
            {
                "id": 4,
                "course_name": "4코스",
                "crops": "귤,감귤",
                "administrative_areas": "표선리,세화리,토산리",
                "eup_myeon_dong_areas": "표선면,남원읍",
            },
            {
                "id": 20,
                "course_name": "20코스",
                "crops": "당근,쪽파",
                "administrative_areas": "김녕리,행원리,평대리,세화리",
                "eup_myeon_dong_areas": "구좌읍",
            },
        ]
    )
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": "감귤",
        "preferred_location": "구좌읍",
        "target_month": 3,
        "include_market_insights": False,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    # 지역이 아니라 작물 단계에서 반려돼야 한다(구좌읍 코스는 존재하지만 감귤을 재배하지 않음).
    assert "감귤" in result["exit_reason"]
    assert "표선" not in (result["exit_reason"] or "")
    assert result["retrieved_chunks"] == []
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []
