"""
api_grandculture.py
=====================
한국향토문화전자대전(디지털여수문화대전 / 디지털순천문화대전) Open API 연동 스크립트.

[현재 상태: 뼈대(skeleton)입니다]
grandculture.net은 robots.txt로 자동 크롤링을 막고 있어 직접 크롤링 대상이
아니며, 대신 공공데이터포털(data.go.kr)에 한국학중앙연구원이 제공하는 정식
Open API가 있습니다. 그런데:
  1) 아직 회원가입/활용신청 및 서비스키 발급 전이라 실제 엔드포인트 URL,
     요청 파라미터명, 응답 필드명을 확인하지 못했습니다.
  2) data.go.kr의 API는 데이터셋마다 파라미터/응답 스펙이 제각각이라,
     추측으로 작성하면 실제 서비스와 다를 가능성이 높습니다.

그래서 아래 코드는 "서비스키 발급 후 채워 넣기만 하면 되는" 뼈대로
작성했습니다. TODO 표시된 부분은 반드시 data.go.kr에서 이 API를
"활용신청"한 뒤 상세설명 페이지에 있는 "API 명세" 탭(요청 변수, 출력 결과
설명, 샘플 URL)을 보고 실제 값으로 교체해야 합니다.

[서비스키 발급 절차 요약]
    1) https://www.data.go.kr 회원가입 (일반 회원으로 충분)
    2) 검색창에 "향토문화전자대전" 또는 "한국학중앙연구원" 검색
    3) 원하는 오픈API 데이터셋 페이지로 이동 → "활용신청" 버튼 클릭
    4) 활용 목적 등 간단한 신청서 작성 (개인/비영리 프로젝트도 보통 즉시
       또는 1~2일 내 자동 승인됨. 심의가 필요한 경우도 있음)
    5) 승인 후 "마이페이지 > 오픈API > 개발계정" 에서 인증키(서비스키) 확인
       - 인증키는 Encoding/Decoding 두 버전이 발급되는데, requests로 직접
         쓸 때는 보통 "Decoding" 키를 사용합니다(라이브러리가 자체적으로
         URL 인코딩을 해주므로). 이중 인코딩 문제가 생기면 Encoding 키로
         바꿔서 시도해보세요.
    6) 데이터셋 상세페이지의 "API 명세" 탭에서 실제 End Point URL, 요청
       파라미터명(지역코드/페이지번호 등), 응답 필드명을 확인하고 아래
       TODO 부분을 채웁니다.
    7) "미리보기" 기능으로 브라우저에서 바로 테스트 호출을 해볼 수 있으니,
       코드를 돌리기 전에 먼저 미리보기로 응답 구조를 한번 확인하는 것을
       추천합니다.

[.env 설정]
    프로젝트 루트에 .env 파일을 만들고 아래처럼 서비스키를 넣으세요.
    (.env는 절대 git에 커밋하지 마세요 - .gitignore에 추가할 것)

        GRANDCULTURE_SERVICE_KEY=발급받은_디코딩_서비스키

사용법:
    1) 서비스키 발급 후 .env 파일 작성
    2) 아래 TODO들을 실제 API 명세에 맞게 수정
    3) `py -3 api_grandculture.py` 실행
"""

import os
import sys

from dotenv import load_dotenv

from config import (
    filter_by_region,
    install_cache,
    make_record,
    new_session,
    polite_sleep,
    report_and_exit,
    save_records,
)

load_dotenv()  # .env 파일의 GRANDCULTURE_SERVICE_KEY=... 를 환경변수로 불러옴

SOURCE_NAME = "한국향토문화전자대전"
LICENSE_NOTE_TEMPLATE = (
    "[이용조건 개별 확인 필요] [출처 : {title} - "
    "한국향토문화전자대전(한국학중앙연구원)]"
)

SERVICE_KEY = os.environ.get("GRANDCULTURE_SERVICE_KEY")

# 한 번 요청에서 최악의 경우에도 무한 루프에 빠지지 않도록 두는 안전 상한.
# totalCount 응답 필드가 없거나 이상하게 크게 와도 이 값을 넘어서면 중단합니다.
MAX_PAGES_SAFETY_CAP = 200

# TODO: data.go.kr "API 명세" 탭에서 확인한 실제 End Point URL로 교체하세요.
# 대부분의 공공데이터포털 REST API는 아래와 비슷한 형태입니다.
#   http://apis.data.go.kr/{기관코드}/{서비스명}/{오퍼레이션명}
API_ENDPOINT = "https://apis.data.go.kr/TODO_기관코드/TODO_서비스명/TODO_오퍼레이션명"

# TODO: 여수/순천에 해당하는 지역코드를 API 명세에서 확인해 채워 넣으세요.
# (한국향토문화전자대전은 "디지털여수문화대전", "디지털순천문화대전"처럼
#  시/군 단위로 별도 하위 사이트를 운영하므로, API에도 이를 구분하는
#  지역코드 또는 지역명 파라미터가 있을 가능성이 높습니다.)
REGION_CODES = {
    "여수": "TODO_여수_지역코드",
    "순천": "TODO_순천_지역코드",
}


def _ensure_configured() -> None:
    """
    API_ENDPOINT/REGION_CODES가 아직 "TODO_"로 채워진 뼈대 상태인 동안에는
    절대 네트워크 요청을 보내지 않도록 막는 가드입니다.
    이 체크가 없으면 서비스키만 넣었을 때 조용히 잘못된 엔드포인트로 요청하게 됩니다.
    data.go.kr에서 실제 API 명세를 확인해 값을 채워 넣기 전까지는 항상 여기서
    NotImplementedError가 발생해야 정상입니다.
    """
    if "TODO_" in API_ENDPOINT:
        raise NotImplementedError(
            "API_ENDPOINT가 아직 TODO 상태입니다. data.go.kr에서 확인한 실제 "
            "End Point URL로 교체한 뒤 다시 실행하세요."
        )
    if any("TODO_" in code for code in REGION_CODES.values()):
        raise NotImplementedError(
            "REGION_CODES에 TODO로 남은 지역코드가 있습니다. API 명세에서 확인한 "
            "실제 지역코드로 교체한 뒤 다시 실행하세요."
        )


def fetch_page(session, region_code: str, page_no: int, num_of_rows: int = 50) -> dict:
    """
    한 페이지 분량의 항목 목록(또는 항목 상세)을 요청합니다.
    TODO: 파라미터명은 실제 API 명세에 맞게 수정하세요.
    공공데이터포털 REST API에서 자주 쓰이는 이름들을 예시로 넣어두었습니다.
    """
    params = {
        "serviceKey": SERVICE_KEY,
        "pageNo": page_no,
        "numOfRows": num_of_rows,
        "type": "json",  # json을 지원하지 않는 API면 xml만 될 수도 있음
        # TODO: 지역 필터 파라미터명 확인 필요 (예: "region", "areaCode" 등)
        "region": region_code,
    }

    resp = session.get(API_ENDPOINT, params=params, timeout=15)
    resp.raise_for_status()

    if not getattr(resp, "from_cache", False):
        polite_sleep()

    # TODO: 응답이 XML이면 resp.text를 xml.etree 등으로 파싱해야 합니다.
    # 아래는 JSON 응답을 가정한 코드입니다.
    return resp.json()


def parse_items(payload: dict) -> list[dict]:
    """
    fetch_page()가 돌려준 원본 응답에서 항목 리스트를 뽑아 공통 스키마로 변환합니다.
    TODO: 아래 필드 경로/이름은 실제 응답 구조를 보고 수정하세요.
    (예시로 흔한 공공데이터포털 응답 형태인 response.body.items.item 을 가정했습니다.)
    """
    records = []

    # TODO: 실제 응답 구조에 맞게 경로 수정
    items = (
        payload.get("response", {})
        .get("body", {})
        .get("items", {})
        .get("item", [])
    )
    if isinstance(items, dict):  # 결과가 1건이면 리스트가 아니라 dict로 오는 API도 있음
        items = [items]

    skipped_no_url = 0
    for item in items:
        # TODO: 실제 필드명으로 교체 (예: title, contents, subject, description 등)
        title = item.get("title", "")
        body = item.get("contents", "")
        # TODO: 실제 원문 URL 필드명 확인 필요. 응답에 URL이 없다면 원문 ID
        # 필드(예: "id", "seq")로 대체 URL(f"{API_ENDPOINT}?id={item_id}" 등)을
        # 만들어서라도 항목마다 고유한 값을 채우세요. URL이 비어 있으면
        # save_records()가 해당 레코드를 저장하지 않습니다(여러 건이 같은
        # 빈 URL로 뭉쳐 마지막 한 건만 남는 사고를 막기 위함).
        source_url = item.get("url", "")

        if not title or not body:
            continue
        if not source_url:
            skipped_no_url += 1
            continue

        records.append(
            make_record(
                title=title,
                body=body,
                source_url=source_url,
                source_name=SOURCE_NAME,
                license_note=LICENSE_NOTE_TEMPLATE.format(title=title),
                data_type="text",
            )
        )

    if skipped_no_url:
        print(f"    [경고] 원문 URL이 없는 항목 {skipped_no_url}건은 건너뛰었습니다.")

    return records


def crawl_region(
    session, region_name: str, region_code: str, num_of_rows: int = 50
) -> list[dict]:
    """
    totalCount(전체 건수)를 기준으로 필요한 만큼 페이지를 순회합니다.
    이전 버전처럼 max_pages를 고정값(10)으로 두면 500건이 넘는 지역에서
    나머지 항목이 조용히 누락되므로, 응답의 totalCount를 우선 사용하고
    (경로는 TODO: 실제 응답 구조 확인 후 수정) 그마저 없으면 "빈 페이지가
    나올 때까지" 방식으로 대체하되 무한 루프 방지를 위해
    MAX_PAGES_SAFETY_CAP을 상한으로 둡니다.
    """
    all_records: list[dict] = []
    total_count = None
    page_no = 1

    while page_no <= MAX_PAGES_SAFETY_CAP:
        print(f"  {region_name} 지역, {page_no}페이지 요청 중...")
        payload = fetch_page(session, region_code, page_no, num_of_rows=num_of_rows)
        records = parse_items(payload)

        if not records:
            break  # 더 이상 결과가 없으면 중단 (마지막 페이지에 도달)

        all_records.extend(records)

        if total_count is None:
            # TODO: 실제 응답 구조에 맞게 totalCount 경로 수정
            total_count = payload.get("response", {}).get("body", {}).get("totalCount")

        if total_count is not None and len(all_records) >= total_count:
            break

        page_no += 1
    else:
        print(
            f"  [경고] {region_name}: 안전 상한({MAX_PAGES_SAFETY_CAP}페이지)에 "
            "도달해 중단했습니다. totalCount 경로가 맞는지 확인하세요."
        )

    return all_records


def main():
    if not SERVICE_KEY:
        print(
            "[안내] GRANDCULTURE_SERVICE_KEY가 설정되지 않았습니다.\n"
            "       data.go.kr에서 서비스키를 발급받은 뒤 .env 파일에 넣어주세요.\n"
            "       (지금은 서비스키 발급 전 상태이므로 여기서 실행을 중단합니다.)"
        )
        sys.exit(1)

    try:
        _ensure_configured()
    except NotImplementedError as e:
        print(f"[안내] {e}")
        sys.exit(1)

    # serviceKey는 캐시 키/저장된 응답에 남지 않도록 제외합니다.
    install_cache(extra_ignored_parameters=("serviceKey",))
    session = new_session()

    all_records: list[dict] = []
    failed = 0
    for region_name, region_code in REGION_CODES.items():
        print(f"[{region_name}] 수집 시작")
        try:
            all_records.extend(crawl_region(session, region_name, region_code))
        except Exception as e:
            failed += 1
            print(f"  [오류] {region_name} 처리 중 문제 발생: {e}")

    kept, dropped = filter_by_region(all_records)
    if dropped:
        print(f"  [필터] 여수/순천과 무관한 {dropped}건은 저장하지 않았습니다.")

    # 지역 하나라도 실패했다면 기존 정상 결과를 보존하며 병합하고, 모두 성공한
    # 경우에만 이번 전체 실행 결과로 교체합니다.
    out_path = save_records(
        kept, "grandculture.json", merge_existing=failed > 0
    )
    report_and_exit(out_path, len(kept), failed)


if __name__ == "__main__":
    main()
