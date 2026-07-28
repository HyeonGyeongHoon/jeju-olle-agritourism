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

    def lte(self, col, value):
        # 시간/거리 상한 필터(2026-07-27 추가)는 호출만 받고 무시합니다 — rdb_ids 생성자
        # 인자가 이미 "필터 후 결과"를 나타내므로 실제 필터링 시뮬레이션은 불필요합니다.
        return self

    def in_(self, col, values):
        # 난이도 상한 필터(2026-07-27 추가)도 lte()와 동일하게 호출만 받고 무시합니다.
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
        # course_name -> {"estimated_time_hours":..., "total_distance_km":..., "difficulty":...}
        # (2026-07-27 추가: _describe_target_course_mismatch 의 시간/거리/난이도 확인 질의용.
        # 기본은 비워 두고, 필요한 테스트만 인스턴스 생성 후 직접 채웁니다.)
        self.time_difficulty_by_course_name = {}
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

    def lte(self, col, value):
        # 시간/거리 상한 필터(2026-07-27 추가)는 RDB 필터 단계(select "id")에서만 걸리고,
        # rdb_ids 생성자 인자가 이미 그 결과를 나타내므로 호출만 받고 무시합니다.
        return self

    def execute(self):
        if self._select_cols == "id":
            return SimpleNamespace(data=[{"id": i} for i in self._rdb_ids])
        if self._select_cols == "course_name,has_wheelchair_segment":
            status = self._wheelchair_status_by_course_name.get(self._eq_course_name)
            if status is None:
                return SimpleNamespace(data=[])
            return SimpleNamespace(data=[{"course_name": self._eq_course_name, "has_wheelchair_segment": status}])
        if self._select_cols == "course_name,estimated_time_hours,total_distance_km,difficulty":
            row = self.time_difficulty_by_course_name.get(self._eq_course_name)
            return SimpleNamespace(data=[row] if row else [])
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


def test_retrieve_rag_node_treats_null_crops_and_areas_as_empty_string_instead_of_crashing():
    """crops/administrative_areas 컬럼 값이 NULL(None)이어도(키 자체는 존재) 청크 조립이
    죽지 않고 빈 문자열로 안전하게 처리되어야 합니다 — `.get(key, "")` 는 키가 있으면 그
    None 값을 그대로 반환해 이후 `_resolve_effective_crops` 의 `.split(",")` 호출에서
    AttributeError 가 났었습니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "1코스", "crops": None, "administrative_areas": None,
                "total_distance_km": 15.0, "estimated_time_text": "5시간", "difficulty": "중",
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
    assert chunks[0]["crops"] == ""
    assert chunks[0]["administrative_areas"] == ""


def test_retrieve_rag_node_exits_early_when_rdb_hard_filter_itself_returns_zero_courses():
    """회귀 방지(2026-07-27, CLAUDE.md "Known remaining gap" 해소): _execute_rdb_filtering
    (휠체어 등 하드 제약 필터) 자체가 이 시점에 이미 0건을 반환하면, 그 뒤의
    target_course/preferred_location/key_item_or_crop 세 필터는 전부 `if <조건> and
    course_ids:` 가드 때문에 진입하지 못해 무조건 반려가 한 번도 걸리지 않고 통과해버렸습니다
    (report_generator 의 더 오래된 일반 "찾을 수 없었습니다" 문구로만 응답되고,
    check_quality_node 의 is_exit_early 조기 단락도 적용되지 않아 재작성 루프까지 낭비).
    이제 이 시점에 이미 0건이면 다른 세 필터와 동일하게 즉시 반려해야 하며, 하드 제약이
    휠체어였다면 그 구체적 사유를 실어야 합니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"wheelchair_required": True},
        "vector_query": "휠체어로 이용 가능한 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "휠체어" in result["exit_reason"]
    assert "생성할 수 없습니다" in result["exit_reason"]
    assert result["retrieved_chunks"] == []
    assert result["culture_chunks"] == []
    assert result["sub_segments"] == []
    assert result["fallback_applied"] is False
    assert result["fallback_reason"] is None
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()
    assert client.rpc_calls == []


def test_retrieve_rag_node_exits_early_with_generic_reason_when_hard_filter_zero_without_wheelchair():
    """하드 제약 필터가 0건인데 휠체어 조건이 켜져 있지 않은 경우(예: DB 조회 자체가 실패해
    빈 결과가 온 경우)는 구체적인 사유를 특정할 수 없으므로, 일반 반려 문구로 fail-fast 해야
    합니다 — 그래도 반려 자체는 걸려야 하며, chunks=[] 로 통과해버리면 안 됩니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed:
        result = retrieve_rag_node(_base_state())

    assert result["is_exit_early"] is True
    assert "생성할 수 없습니다" in result["exit_reason"]
    mock_embed.assert_not_called()


# --- 시간/거리/난이도 상한 하드 제약 Fail-Fast (2026-07-27 추가) ---
# 사용자가 명시한 시간/거리/난이도 상한이 courses 실제 범위를 완전히 벗어나면(현재 실측:
# estimated_time_hours 2.0~7.0시간, total_distance_km 4.2~20.9km), _execute_rdb_filtering
# (여기서는 _FakeCoursesTable(rdb_ids=[]) 로 "그 결과 0건"을 직접 시뮬레이션)이 빈 리스트를
# 반환하고, 그 즉시(wheelchair_required 와 동일한 최상단 체크) 반려되어야 합니다.


def test_retrieve_rag_node_exits_early_when_max_time_hours_matches_no_course():
    """사용자 요청: "1시간 이내로 다녀올 수 있는 코스로 기획서 써줘"처럼 DB 전체 범위(2.0~
    7.0시간)보다 짧은 시간 상한을 요청하면 즉시 반려되어야 하며, 사유에 시간 조건이
    구체적으로 언급돼야 합니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"max_time_hours": 1.0},
        "vector_query": "1시간 이내 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "1.0시간 이내" in result["exit_reason"]
    assert "생성할 수 없습니다" in result["exit_reason"]
    assert result["retrieved_chunks"] == []
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()


def test_retrieve_rag_node_exits_early_when_allowed_difficulties_match_no_course():
    """사용자 요청: "난이도 하 코스만" 처럼 요구한 난이도에 해당하는 코스가 하나도 없으면
    즉시 반려되어야 하며, 사유에 난이도 조건이 구체적으로 언급돼야 합니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"allowed_difficulties": ["하"]},
        "vector_query": "난이도 낮은 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "난이도 '하'인" in result["exit_reason"]
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()


def test_retrieve_rag_node_zero_match_reason_lists_multiple_allowed_difficulties():
    """허용 난이도가 여러 개면 반려 사유에 자연스럽게 나열돼야 합니다("난이도 '하' 또는 '중'인").
    LLM 이 넘긴 순서와 무관하게 쉬운 순서로 정규화된 표기여야 합니다."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"allowed_difficulties": ["중", "하"]},
        "vector_query": "무난한 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "난이도 '하' 또는 '중'인" in result["exit_reason"]


def test_retrieve_rag_node_combines_time_and_difficulty_in_zero_match_reason():
    """시간과 난이도가 동시에 지정되고 둘 다 0건이면, 반려 사유 한 문장에 두 조건이 모두
    포함돼야 합니다(사용자 예시: "2시간 이내 극도로 어려운 코스" → 모순된 조합이라 반려)."""
    courses_table = _FakeCoursesTable(rdb_ids=[], course_meta_by_id={})
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"max_time_hours": 2.0, "allowed_difficulties": ["상"]},
        "vector_query": "2시간 이내 극도로 어려운 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "2.0시간 이내" in result["exit_reason"]
    assert "난이도 '상'인" in result["exit_reason"]
    mock_embed.assert_not_called()


def test_retrieve_rag_node_normal_flow_when_time_constraint_is_satisfiable():
    """회귀 방지: 시간 상한이 DB 실제 범위 안에 있어 매칭되는 코스가 있으면, 기존 정상
    플로우(벡터 검색까지)가 그대로 동작해야 합니다 — 새 필터가 만족 가능한 조건까지
    반려로 오판하면 안 됩니다."""
    courses_table = _FakeCoursesTable(
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "10-1코스", "crops": "감귤", "administrative_areas": "가파리",
                "total_distance_km": 4.2, "estimated_time_hours": 2.0,
                "estimated_time_text": "2시간", "difficulty": "하",
            },
        },
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[{"id": 10, "course_id": 1, "title": "청크", "content": "...", "similarity": 0.9}],
    )

    state = _base_state()
    state["parsed_constraints"] = {
        "hard_constraints": {"max_time_hours": 3.0, "allowed_difficulties": ["하"]},
        "vector_query": "3시간 이내 쉬운 코스",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is False
    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["course_name"] == "10-1코스"


def test_retrieve_rag_node_describes_target_course_time_mismatch_specifically():
    """사용자 요청: "1코스인데 1시간 이내로 기획서 써줘"처럼, 지정한 코스는 실존하지만 그 코스의
    실제 소요시간이 요청한 상한을 초과해 하드 필터에서 빠지는 경우("시간 상한을 만족하는 다른
    코스는 있지만 1코스는 아님"), '코스명을 못 찾았다'는 일반 문구 대신 실제 수치를 근거로 한
    구체적 사유를 반환해야 합니다(휠체어 케이스와 동일한 설계)."""
    courses_table = _FakeCoursesTableForTargetCourseHardConstraint(
        rdb_ids=[10],
        course_rows_by_id={10: {"course_name": "10-1코스", "administrative_areas": "가파리"}},
        wheelchair_status_by_course_name={},
    )
    courses_table.time_difficulty_by_course_name = {
        "1코스": {
            "course_name": "1코스", "estimated_time_hours": 4.5,
            "total_distance_km": 15.1, "difficulty": "중",
        },
    }
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["target_course"] = "1코스"
    state["parsed_constraints"] = {
        "hard_constraints": {"max_time_hours": 1.0},
        "vector_query": "1코스 1시간 이내",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]) as mock_culture:
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "1코스" in result["exit_reason"]
    assert "4.5시간" in result["exit_reason"]
    assert "1.0시간 이내" in result["exit_reason"]
    mock_embed.assert_not_called()
    mock_culture.assert_not_called()


def test_retrieve_rag_node_describes_target_course_difficulty_mismatch_specifically():
    """지정한 코스는 실존하지만 그 코스의 실제 난이도가 요청한 허용 난이도 목록에 없으면
    ("1코스는 난이도 중인데 사용자는 '상'만 요청"), 상한 초과 문구가 아니라 "난이도 조건에
    포함되지 않는다"는 목록 기준 문구로 구체적 사유를 반환해야 합니다."""
    courses_table = _FakeCoursesTableForTargetCourseHardConstraint(
        rdb_ids=[10],
        course_rows_by_id={10: {"course_name": "10-1코스", "administrative_areas": "가파리"}},
        wheelchair_status_by_course_name={},
    )
    courses_table.time_difficulty_by_course_name = {
        "1코스": {
            "course_name": "1코스", "estimated_time_hours": 4.5,
            "total_distance_km": 15.1, "difficulty": "중",
        },
    }
    client = _FakeClient(courses_table, rpc_data=[])

    state = _base_state()
    state["target_course"] = "1코스"
    state["parsed_constraints"] = {
        "hard_constraints": {"allowed_difficulties": ["상"]},
        "vector_query": "1코스 난이도 상",
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]) as mock_embed, \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    assert result["is_exit_early"] is True
    assert "1코스" in result["exit_reason"]
    assert "난이도(중)" in result["exit_reason"]
    assert "난이도 조건(상)" in result["exit_reason"]
    mock_embed.assert_not_called()


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


class _FakeCoursesTableForCropNormalization:
    def __init__(self, crops_list, rdb_ids, course_meta_by_id):
        self.crops_list = crops_list
        self._rdb_ids = rdb_ids
        self._course_meta_by_id = course_meta_by_id
        self._cols = None
        self._eq_id = None

    def select(self, cols):
        self._cols = cols
        return self

    def eq(self, col, value):
        if col == "id":
            self._eq_id = value
        return self

    def in_(self, col, values):
        return self

    def execute(self):
        if self._cols == "crops":
            return SimpleNamespace(data=[{"crops": c} for c in self.crops_list])
        if self._cols == "id":
            return SimpleNamespace(data=[{"id": i} for i in self._rdb_ids])
        if self._cols == "id,crops":
            return SimpleNamespace(data=[{"id": i, "crops": self._course_meta_by_id[i]["crops"]} for i in self._rdb_ids])
        meta = self._course_meta_by_id.get(self._eq_id)
        return SimpleNamespace(data=[meta] if meta else [])


def test_retrieve_rag_node_normalizes_crop_name_from_yuchae_flower():
    """'유채꽃'으로 질의 시 DB에 알려진 작물 태그인 '유채'로 정규화되어,
    반려되지 않고 정상적으로 기획서 작성이 가능한지 검증합니다."""
    courses_table = _FakeCoursesTableForCropNormalization(
        crops_list=["유채", "당근"],
        rdb_ids=[1],
        course_meta_by_id={
            1: {
                "course_name": "2코스",
                "crops": "유채",
                "administrative_areas": "신풍리",
                "total_distance_km": 10.0,
                "estimated_time_text": "3시간",
                "difficulty": "중",
            }
        }
    )
    client = _FakeClient(
        courses_table,
        rpc_data=[
            {"id": 10, "course_id": 1, "title": "유채꽃 만발 코스", "content": "유채꽃이 아름다운...", "similarity": 0.9}
        ]
    )

    state = _base_state()
    state["b2b_params"] = {
        "key_item_or_crop": "유채꽃",
        "preferred_location": None,
        "target_month": 3,
        "include_market_insights": False,
    }

    with patch.object(nodes, "get_supabase_client", return_value=client), \
         patch.object(nodes, "get_solar_embedding", return_value=[0.1]), \
         patch.object(nodes, "_search_culture_knowledge", return_value=[]):
        result = retrieve_rag_node(state)

    # 1. Fail-Fast 조기 반려되지 않고 정상 결과가 리턴되었는지 검증
    assert result.get("is_exit_early") is not True
    assert len(result["retrieved_chunks"]) == 1
    assert result["retrieved_chunks"][0]["course_name"] == "2코스"
    
    # 2. b2b_params의 key_item_or_crop 이 '유채'로 정규화되었는지 검증
    assert result.get("b2b_params") is not None
    assert result["b2b_params"]["key_item_or_crop"] == "유채"

