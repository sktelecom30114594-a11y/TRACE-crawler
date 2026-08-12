"""
config.py
=========
TRACE 프로젝트의 모든 수집 스크립트(api_grandculture.py, crawler_encykorea.py,
crawler_folkency.py, crawler_nculture.py)가 공통으로 사용하는 설정과 헬퍼 함수를 모아둔 파일입니다.

크롤링이 처음이라면 아래 개념만 기억하면 됩니다.
1) 상대방 서버에 너무 빠르게, 너무 많이 요청하지 않는다 (딜레이)
2) 내가 누구인지 밝힌다 (User-Agent에 연락처 명시)
3) 같은 페이지를 두 번 다운로드하지 않는다 (requests-cache)
4) 수집 결과는 항상 같은 모양(JSON 스키마)으로 저장한다
"""

import json
import os
import random
import sys
import time
from datetime import date
from pathlib import Path

import requests
import requests_cache
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()

# ---------------------------------------------------------------------------
# 1. 크롤러 정체성 / 매너 설정
# ---------------------------------------------------------------------------

# 이 크롤러가 누구인지 밝히는 값입니다. 공공기관 사이트 담당자가 로그를 보다가
# 문제가 생기면 이 문자열을 보고 연락할 수 있어야 합니다. 공개 저장소에 개인
# 이메일을 남기지 않도록 환경변수로 받습니다.
CONTACT_EMAIL = os.environ.get("CRAWLER_CONTACT_EMAIL", "your-email@example.com")
USER_AGENT = f"TRACE-project-crawler (educational/non-commercial research; contact: {CONTACT_EMAIL})"

# 요청 사이에 줄 딜레이(초). 매 요청마다 이 범위에서 랜덤하게 하나를 골라 sleep합니다.
# 랜덤으로 주는 이유: 모든 요청이 정확히 1.5초 간격이면 오히려 "봇처럼" 보이기 쉽고,
# 서버 입장에서도 일정한 간격보다 약간 흔들리는 편이 부담이 덜합니다.
REQUEST_DELAY_RANGE = (1.5, 2.0)

# 429(Too Many Requests)나 5xx 응답을 받았을 때 재시도할 횟수와 백오프 배율.
# urllib3 Retry가 내부적으로 지수 백오프(backoff_factor * 2**(재시도횟수-1))로 대기하며,
# 서버가 Retry-After 헤더를 보내면 그 값을 우선 따릅니다.
RETRY_TOTAL = 3
RETRY_BACKOFF_FACTOR = 2
RETRY_STATUS_FORCELIST = (429, 500, 502, 503, 504)

# requests-cache가 기본적으로 가리는 인증 관련 파라미터입니다. 프로젝트별로
# 추가 파라미터를 지정하더라도 이 기본 목록이 사라지지 않도록 함께 전달합니다.
CACHE_IGNORED_PARAMETERS = (
    "Authorization",
    "Proxy-Authorization",
    "X-API-Key",
    "X-Auth-Token",
    "X-API-Token",
    "X-Access-Token",
    "access_token",
    "api_key",
    "apikey",
)

# 이 프로젝트의 최종 수집 대상 지역. 크롤러가 실제로 저장하기 직전에
# 제목+본문에 이 키워드가 하나도 없는 레코드는 걸러내는 안전장치로 씁니다.
# (예: 지역N문화 태그 페이지 하나에 완도/신안 등 다른 지역 이야기가 섞여 나오는 경우)
REGION_KEYWORDS = ("여수", "순천")

# ---------------------------------------------------------------------------
# 2. 경로 / 저장소 설정
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# requests-cache가 만드는 캐시 DB 파일 (sqlite). 한 번 받은 페이지는 이 파일에
# 저장되고, 같은 URL을 다시 요청하면 실제 네트워크 요청 없이 캐시에서 즉시 반환됩니다.
CACHE_DB_PATH = BASE_DIR / "http_cache.sqlite"

# 캐시를 얼마나 오래 유지할지(초). 7일 = 60*60*24*7
# 원본 사이트 내용이 자주 안 바뀌는 아카이브성 데이터이므로 길게 잡아도 무방합니다.
CACHE_EXPIRE_AFTER = 60 * 60 * 24 * 7


# ---------------------------------------------------------------------------
# 3. 공통 함수
# ---------------------------------------------------------------------------

def install_cache(
    extra_allowable_methods: tuple[str, ...] = (),
    extra_ignored_parameters: tuple[str, ...] = (),
) -> None:
    """
    requests-cache를 전역으로 설치합니다.
    이 함수를 한 번 호출하면, 이후 이 프로세스 안에서 `requests.get(...)`으로
    보내는 모든 요청이 자동으로 캐시를 거치게 됩니다.
    (참고: crawler_encykorea.py, api_grandculture.py처럼 requests를 직접 쓰는
     스크립트에서만 필요합니다. Playwright를 쓰는 경우는 해당 없음.)

    requests-cache는 기본적으로 GET/HEAD만 캐시합니다. crawler_folkency.py처럼
    (읽기 전용인) 내부 API를 POST로 호출하는 경우에만
    extra_allowable_methods=("POST",)를 넘겨 캐시 대상에 추가하세요.
    다른 크롤러까지 POST를 캐시하게 만들면 의도치 않은 요청까지 캐시될 수 있으므로
    필요한 스크립트에서만 켜야 합니다.

    api_grandculture.py처럼 서비스키를 쿼리 파라미터(serviceKey)로 넘기는 경우엔
    extra_ignored_parameters=("serviceKey",)를 넘겨, 캐시 키 계산과 저장된 응답 모두에서
    키 값이 남지 않도록 하세요.
    """
    requests_cache.install_cache(
        cache_name=str(CACHE_DB_PATH.with_suffix("")),  # 확장자는 backend가 붙여줌
        backend="sqlite",
        expire_after=CACHE_EXPIRE_AFTER,
        allowable_methods=("GET", "HEAD", *extra_allowable_methods),
        ignored_parameters=(*CACHE_IGNORED_PARAMETERS, *extra_ignored_parameters),
    )


def new_session() -> requests.Session:
    """
    공통 User-Agent 헤더가 세팅된 requests.Session을 하나 만들어 돌려줍니다.
    캐시를 쓰고 싶다면 install_cache()를 먼저 호출한 뒤 이 함수를 쓰세요.

    429/5xx 응답에 대해서는 어댑터 단에서 자동으로 재시도(지수 백오프,
    Retry-After 헤더 우선 반영)하므로 각 크롤러 코드에서 따로 재시도 로직을
    작성할 필요가 없습니다.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9",
        }
    )

    retry = Retry(
        total=RETRY_TOTAL,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_FORCELIST,
        # 이 프로젝트에서 사용하는 GET/HEAD와 읽기 전용 POST만 재시도합니다.
        # PUT/PATCH/DELETE 같은 변경 메서드까지 자동 재시도하지 않습니다.
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


def polite_sleep() -> None:
    """요청 하나를 보낸 뒤 호출하세요. REQUEST_DELAY_RANGE 사이의 랜덤 시간만큼 대기합니다."""
    time.sleep(random.uniform(*REQUEST_DELAY_RANGE))


def make_record(
    title: str,
    body: str,
    source_url: str,
    source_name: str,
    license_note: str,
    data_type: str = "text",
) -> dict:
    """
    모든 크롤러가 공통으로 사용하는 결과 레코드 하나를 만듭니다.
    요청사항에 정의된 스키마:
    {제목, 본문, 출처URL, 출처사이트명, 수집일, 라이선스/출처표기문구, 데이터유형}
    """
    return {
        "제목": title.strip() if title else "",
        "본문": body.strip() if body else "",
        "출처URL": source_url,
        "출처사이트명": source_name,
        "수집일": date.today().isoformat(),
        "라이선스_출처표기문구": license_note,
        "데이터유형": data_type,
    }


def save_records(
    records: list[dict],
    filename: str,
    merge_existing: bool = True,
    allow_empty_snapshot: bool = False,
) -> Path:
    """
    레코드 리스트를 output/ 폴더 아래 JSON 파일로 저장합니다.
    merge_existing=True이면 기존 파일과 출처URL 기준으로 병합합니다. 사람이 URL을
    조금씩 추가하는 encykorea 수집에 적합합니다. 자동 검색으로 전체 후보를 다시
    구하는 크롤러는 완전한 실행에 성공했을 때 merge_existing=False로 저장해야
    이전 실행에만 존재하던 오래된 항목도 정리됩니다.

    단, 스냅샷 실행이 일시적인 검색 장애로 0건을 반환했을 때 기존 정상 결과를
    빈 배열로 덮지 않도록 기본적으로 빈 스냅샷 교체는 거부합니다. 정말로 결과를
    비워야 하는 호출자만 allow_empty_snapshot=True를 명시해야 합니다.

    출처URL이 빈 문자열인 레코드는 저장하지 않습니다. 여러 건이 모두 빈 URL이면
    전부 같은 키("")로 취급되어 마지막 한 건만 남는 문제가 생기기 때문입니다.
    (원문에 개별 URL이 없는 API 응답이라면, 파서 쪽에서 원문 ID 등을 조합해
    고유한 출처URL을 만들어 넘기세요.)
    """
    out_path = OUTPUT_DIR / filename

    if (
        not merge_existing
        and not records
        and out_path.exists()
        and not allow_empty_snapshot
    ):
        print(
            "  [경고] 새 스냅샷이 0건이므로 기존 결과 파일을 보존합니다. "
            "검색/API 응답을 확인하세요."
        )
        return out_path

    existing: list[dict] = []
    if merge_existing and out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            existing = json.load(f)

    merged_by_url = {r["출처URL"]: r for r in existing if r.get("출처URL")}
    skipped = 0
    for r in records:
        url = r.get("출처URL", "")
        if not url:
            skipped += 1
            continue
        merged_by_url[url] = r  # 새 결과로 덮어쓰기 (재수집 시 최신화)

    if skipped:
        print(f"  [경고] 출처URL이 없는 레코드 {skipped}건은 저장하지 않았습니다.")

    merged = list(merged_by_url.values())

    # 임시 파일에 쓴 뒤 원자적으로 교체합니다. 쓰는 도중 프로세스가 죽어도
    # 기존 out_path는 손상되지 않고 그대로 남습니다.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    tmp_path.replace(out_path)

    return out_path


def filter_by_region(
    records: list[dict], keywords: tuple[str, ...] = REGION_KEYWORDS
) -> tuple[list[dict], int]:
    """
    제목+본문에 keywords 중 하나도 포함되지 않은 레코드를 걸러냅니다.
    (kept, dropped_count)를 돌려줍니다.

    구조 확인 삼아 넣어본 태그/문서에서 다른 지역 이야기가 섞여 나올 수 있으므로,
    save_records()로 저장하기 직전에 항상 이 함수를 거치는 것을 권장합니다.
    """
    kept = []
    dropped = 0
    for r in records:
        haystack = (r.get("제목", "") or "") + (r.get("본문", "") or "")
        if any(k in haystack for k in keywords):
            kept.append(r)
        else:
            dropped += 1
    return kept, dropped


def report_and_exit(
    out_path: Path, saved_count: int, failed_count: int, min_success: int = 1
) -> None:
    """
    실행 결과를 요약 출력하고, 성공 건수가 min_success 미만이면 종료 코드 1로
    비정상 종료합니다. 그래야 "완료"라는 출력만 보고 낡은 결과를 최신 성공
    결과로 오인하는 일을 막을 수 있습니다.
    """
    print(f"\n완료: {saved_count}건 저장 (실패 {failed_count}건) -> {out_path}")
    if saved_count < min_success:
        print(
            f"[오류] 성공 건수({saved_count})가 최소 기준({min_success}건)보다 "
            "적습니다. 비정상 종료합니다."
        )
        sys.exit(1)
