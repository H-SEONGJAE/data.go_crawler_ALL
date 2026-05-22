# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for 기관별 조회수/다운로드 수 수집.

리팩토링 기준
- 기존 Selenium 기반 crawler.py 페이지네이션 클릭 방식은 사용하지 않는다.
- crawler_metadata.py에서 검증된 목록 카드 파싱 함수
  collect_dataset_links_from_html()를 그대로 활용한다.
- 결과 컬럼은 기존과 동일하게 유지한다: 데이터명 / 조회수 / 다운로드수
- 상세 메타데이터 수집은 하지 않고, 목록 카드에서 조회수/다운로드 수만 추출한다.
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

from org_url_resolver import (
    build_org_filter_url,
    pick_url_candidates_for_collection,
    resolve_org_name_and_url_fast,
)

from crawler_metadata import (
    build_http_headers,
    collect_dataset_links_from_html,
    clean_dataset_title,
    optimize_list_url,
)

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

BASE_LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
LIST_PER_PAGE = 1000
REQUEST_TIMEOUT = 25
PAGE_SLEEP_SEC = 0.15
MAX_EMPTY_PAGES = 1
MAX_PAGES_GUARD = 300


def resolve_org_for_stats(org_input: str, target_url: str = "") -> tuple[str, list[str], list[str]]:
    """
    조회수/다운로드 수 수집용 기관명과 URL 후보를 확정한다.
    - (주), (재) 같은 후보를 임의 생성하지 않는다.
    - 포털 목록에서 실제 제공기관명을 추출해 그 원문으로 URL을 만든다.
    - UI가 target_url을 넘겨주면 해당 URL을 최우선으로 사용한다.
    """
    raw = (org_input or "").strip()
    if target_url and target_url.strip():
        return raw, [], [target_url.strip()]

    result = resolve_org_name_and_url_fast(
        raw,
        headers=build_http_headers(),
        timeout=5,
        per_page=10,
        max_workers=4,
    )
    exact_org = result.get("exact_org") or raw
    candidates = result.get("candidates", []) or []

    resolved_url = build_org_filter_url(exact_org, current_page=1, per_page=LIST_PER_PAGE)
    urls = pick_url_candidates_for_collection(exact_org, resolved_url=resolved_url, per_page=LIST_PER_PAGE)

    # 포털 제공기관 추출이 실패한 경우에만 원 입력값도 fallback으로 확인한다.
    if not result.get("found") and raw != exact_org:
        urls.extend(pick_url_candidates_for_collection(raw, resolved_url="", per_page=LIST_PER_PAGE))

    return exact_org, candidates, list(dict.fromkeys(urls))

def to_int_or_blank(value):
    s = str(value or "").strip().replace(",", "")
    if not s:
        return ""
    try:
        return int(s)
    except Exception:
        return s


def collect_stats_by_metadata_list_parser(target_url: str, session: requests.Session) -> list[dict]:
    """
    crawler_metadata.py의 목록 HTML 파서로 조회수/다운로드 수를 수집한다.
    페이지 버튼 클릭 없이 currentPage 파라미터를 직접 증가시켜 페이지 누락을 방지한다.
    """
    rows = []
    seen_urls = set()
    empty_pages = 0

    for page_no in range(1, MAX_PAGES_GUARD + 1):
        list_url = optimize_list_url(target_url, per_page=LIST_PER_PAGE, current_page=page_no)
        print(f"[LIST] page {page_no:03d} 요청", flush=True)

        resp = session.get(list_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        items = collect_dataset_links_from_html(resp.text, list_url)
        print(f"[LIST] page {page_no:03d} +{len(items):,}건", flush=True)

        if not items:
            empty_pages += 1
            if empty_pages >= MAX_EMPTY_PAGES:
                break
            continue

        empty_pages = 0

        new_count = 0
        for item in items:
            detail_url = (item.get("detail_url") or "").strip()
            if not detail_url or detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            title = clean_dataset_title(item.get("title") or item.get("raw_title") or "")
            if not title:
                title = detail_url

            rows.append({
                "데이터명": title,
                "조회수": to_int_or_blank(item.get("조회수", "")),
                "다운로드수": to_int_or_blank(item.get("다운로드수", "") or item.get("다운로드(바로가기)", "")),
            })
            new_count += 1

        print(f"[LIST] page {page_no:03d} 신규 {new_count:,}건 | 누적 {len(rows):,}건", flush=True)

        if new_count == 0:
            # 현재 페이지가 모두 중복이면 다음 페이지부터 반복될 가능성이 높음
            break

        time.sleep(PAGE_SLEEP_SEC)

    return rows


def main():
    parser = argparse.ArgumentParser(description="기관별 조회수/다운로드 수 수집 wrapper")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--target-url", default="", help="UI에서 이미 확정한 기관별 파일데이터 URL")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    org_input = args.org_name.strip()
    exact_org, candidates, target_urls = resolve_org_for_stats(org_input, args.target_url)

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - stats_runner]", flush=True)
    print(f"- org_name: {org_input}", flush=True)
    print(f"- resolved_org_name: {exact_org}", flush=True)
    if candidates:
        print(f"- org_candidates: {candidates}", flush=True)
    print(f"- target_url_count: {len(target_urls)}", flush=True)
    print("※ crawler_metadata.py의 목록 파서로 조회수/다운로드 수를 수집합니다.", flush=True)
    print("※ 결과 컬럼은 기존과 동일하게 데이터명 / 조회수 / 다운로드수로 저장합니다.", flush=True)
    print("=" * 80, flush=True)

    session = requests.Session()
    session.headers.update(build_http_headers())

    best = {
        "org": exact_org,
        "url": "",
        "rows": [],
        "error": None,
    }

    for target_url in target_urls:
        print("\n" + "-" * 80, flush=True)
        print(f"[URL 후보] {target_url}", flush=True)
        try:
            rows = collect_stats_by_metadata_list_parser(target_url, session)
            print(f"[후보 결과] {exact_org} / {len(rows):,}건", flush=True)
            if len(rows) > len(best["rows"]):
                best = {"org": exact_org, "url": target_url, "rows": rows, "error": None}

            # 첫 번째 URL에서 정상 수집되면 속도를 위해 추가 후보 확인을 생략한다.
            if rows:
                break
        except Exception as e:
            print(f"[경고] 후보 수집 실패: {repr(e)}", flush=True)
            best["error"] = repr(e)
            continue

    if not best["rows"]:
        raise RuntimeError(f"모든 기관/URL 후보에서 수집 결과가 0건입니다. 마지막 오류: {best.get('error')}")

    df = pd.DataFrame(best["rows"], columns=["데이터명", "조회수", "다운로드수"])

    # URL 후보가 중복 결과를 만들 가능성을 방지하기 위해 최종 데이터명 기준 중복 제거는 하지 않는다.
    # 같은 이름의 파일데이터가 실제로 존재할 수 있으므로 detail_url 기준 중복은 수집 단계에서만 처리한다.

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_org_name = best["org"].replace("(", "_").replace(")", "")
    excel_path = output_dir / f"공공데이터_{safe_org_name}_조회수_다운로드수_{timestamp}.xlsx"

    with pd.ExcelWriter(excel_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    result = {
        "status": "completed",
        "org_name": best["org"],
        "target_url": best["url"],
        "row_count": int(len(df)),
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 80, flush=True)
    print("[저장 완료]", flush=True)
    print(f"- 사용 기관명: {best['org']}", flush=True)
    print(f"- 수집 건수: {len(df):,}", flush=True)
    print(f"- 저장 파일: {excel_path}", flush=True)
    print("=" * 80, flush=True)


if __name__ == "__main__":
    main()
