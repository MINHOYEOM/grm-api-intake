"""외부 HTTP 호스트별 요청 간격과 재시도 기본값.

www.fda.gov 는 실행 경로마다 기존 요청 간격이 다르다. 각 경로가 기존
대기 위치와 CLI 재정의를 유지할 수 있도록 기본값만 여기서 공유한다.
"""

# www.fda.gov — 초 단위.
FDA_483_PDF_DELAY_SECONDS = 0.5  # 일일 483 PDF 및 단건 PDF 백필·회수
FDA_BACKFILL_DELAY_SECONDS = 30  # 483/WL 전수 백필 및 483 전문 백필
FDA_483_MIXED_PDF_DELAY_SECONDS = 0.3  # 혼합 PDF 스캔의 함수 기본값
FDA_WL_RECOVERY_DELAY_SECONDS = 5.0
FDA_PUBLISHED_SOURCE_CHECK_DELAY_SECONDS = 0.5

# 공통 HTTP GET 헬퍼를 쓰는 FDA 경로의 추가 재시도 횟수.
FDA_HTTP_RETRIES = 3  # 483 목록·PDF와 WL 목록
FDA_GUIDANCE_HTTP_RETRIES = 2
FDA_483_MIXED_PDF_HTTP_RETRIES = 2

# WL 본문 전용 재시도. 연결 오류·타임아웃도 재시도한다.
FDA_WL_BODY_FETCH_ATTEMPTS = 3
FDA_WL_BODY_RETRY_BACKOFF_SECONDS = (2.0, 5.0)
FDA_WL_BODY_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# www.accessdata.fda.gov — 수입경보 요청은 별도 대기 없이 재시도한다.
FDA_IMPORT_ALERT_FETCH_ATTEMPTS = 3

# api-datadashboard.fda.gov — 실사 분류·지적사항 POST 정책.
FDA_DDAPI_HTTP_RETRIES = 2
FDA_DDAPI_RETRY_BACKOFF_BASE_SECONDS = 2

# api.notion.com — 수집 결과 적재 간 최소 간격.
NOTION_WRITE_DELAY_SECONDS = 0.34

# 빈칸 백필(backfill_blank_excerpts.py) — 수집 완전성 점검 11-22 예의 규칙: WHO 5초, gov.uk·EMA·ECA 2초.
# 11-25 dry 실측: EMA 를 1초 간격으로 읽자 429(Too Many Requests)가 연달아 났다.
BACKFILL_WHO_DELAY_SECONDS = 5.0
BACKFILL_ARTICLE_DELAY_SECONDS = 2.0
BACKFILL_429_RETRY_WAITS_SECONDS = (30.0, 60.0)  # Retry-After 가 없을 때 대기
