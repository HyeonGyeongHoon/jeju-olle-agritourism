# 로컬 개발 및 실행 환경 구축 가이드

본 문서는 제주올레 도슨트 RAG 에이전트 MVP 개발을 위해 로컬 개발 환경(Windows OS) 을 셋업하는 방법을 상세히 안내합니다.

## 1. Windows 파이썬 실행 오류 해결 (앱 실행 별칭 비활성화)
윈도우 환경에서 `python` 이나 `python3` 명령 실행 시 Microsoft Store 가 열리거나 `Python` 이라는 메시지만 출력된 채 비정상 종료(Exit Code 1) 되는 문제를 해결합니다.
- **원인**: Windows 시스템 이 실제 설치된 파이썬보다 Microsoft Store 앱 설치 관리자 바로가기(`python.exe`) 를 먼저 인식하기 때문입니다.
- **해결 절차**:
  1. Windows 작업 표시줄 검색창에 **앱 실행 별칭 관리** (또는 '앱 실행 별칭') 를 입력하여 설정 창으로 진입합니다.
  2. 목록 내에서 `python.exe` 와 `python3.exe` (앱 설치 관리자) 항목 을 찾아 **끔** (Off) 으로 변경합니다.
  3. 설정 완료 후 PowerShell 창을 완전히 닫고 다시 실행합니다.

## 2. Python 런타임 설치
- **설치 버전**: **Python 3.10.x** 이상 권장 (Target Version 3.10)
- **설치 링크**: [Python 공식 다운로드 페이지](https://www.python.org/downloads/)
- **설치 시 주의 사항**:
  - 설치 마법사(Installer) 첫 화면 하단에 있는 **Add python.exe to PATH** 옵션 을 반드시 체크하고 설치를 진행해야 합니다. 이 옵션을 누락하면 수동으로 환경변수를 수정해야 합니다.

## 3. 프로젝트 가상환경 구축 및 의존성 설치
Windows PowerShell 환경에서 독립적인 가상환경(venv) 을 만들고 프로젝트에 필요한 의존성 라이브러리를 설치하는 과정입니다.

### 1) 가상환경 생성
프로젝트 루트 디렉토리([jeju-olle-docent](../) 로 이동한 후 다음 명령어를 실행합니다.
```powershell
python -m venv .venv
```

### 2) PowerShell 실행 정책 변경 (최초 1회 필수)
가상환경 활성화 스크립트 실행이 차단되는 보안 정책을 우회하기 위해 권한을 일시 조정합니다.
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

### 3) 가상환경 활성화
```powershell
.venv\Scripts\Activate.ps1
```
활성화가 정상적으로 완료되면 터미널 좌측에 `(.venv)` 표시 가 생성됩니다.

### 4) 의존성 라이브러리 설치
[requirements.txt](../requirements.txt) 에 명시된 2주 스프린트 용 패키지들을 최신 상태로 설치합니다.
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 4. 로컬 테스트 하네스 검증 및 린트(Lint) 실행
가상환경 활성화가 완료된 상태에서 파서 및 코드 무결성을 검증합니다.

### 1) 휠체어 구간 파서 무결성 테스트 실행
단일 PDF 및 Mock Fixture 를 기반으로 정형 데이터를 가로채 무결성 단언을 수행합니다.
```powershell
pytest tests/test_wheelchair.py -s
```
- `-s` 옵션 은 파서 내부에서 발생하는 `[Ingestion-Warning]` 등 의 경고 및 스킵 로그를 실시간으로 터미널에 출력해 줍니다.

### 2) 코드 품질 검사 (Ruff)
Ruff 를 활용하여 코드의 스타일 검사(Lint) 및 자동 포맷팅(Format) 을 수행합니다.
```powershell
# 코드 오류 및 컨벤션 검사
ruff check .

# 코드 포맷 자동 교정
ruff format .
```

## 5. Supabase Cloud 및 API 연동 설정 (.env)
로컬 인제스천 엔진이 Supabase RDB 및 OpenAI 임베딩 API 와 통신하기 위한 환경 변수를 셋업합니다.
- **환경 변수 파일 생성**:
  [jeju-olle-docent](../) 루트 디렉토리에 `.env` 파일 을 신규 생성하고 아래 키를 채웁니다.
  ```env
  SUPABASE_URL=your_supabase_project_url_here
  SUPABASE_KEY=your_supabase_anon_or_service_key_here
  OPENAI_API_KEY=your_openai_api_key_here
  ```
  참고용 예시 템플릿은 [.env.example](../.env.example) 파일 을 참고하시기 바랍니다.
