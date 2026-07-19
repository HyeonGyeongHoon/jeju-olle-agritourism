from src.ingestion.database_loader import load_courses_to_db, load_wheelchair_segments_to_db

def test_database_loaders():
    # 적재 로직의 스텁 호출이 불리언 값을 반환하는지 테스트
    assert load_courses_to_db([]) is True
    assert load_wheelchair_segments_to_db([]) is True
