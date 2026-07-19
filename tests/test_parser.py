from src.ingestion.parser import parse_courses, chunk_by_subtitle

def test_parse_courses():
    dummy_text = "Course 01 Start point: A, End point: B. Distance: 15km."
    courses = parse_courses(dummy_text)
    assert isinstance(courses, list)

def test_chunk_by_subtitle():
    dummy_course_text = "Course 01 Content ― Subtitle 1 Content ― Subtitle 2 Content"
    chunks = chunk_by_subtitle(dummy_course_text)
    assert isinstance(chunks, list)
