# -*- coding: utf-8 -*-
"""
기관별 조회수/다운로드 수 수집 wrapper.

변경 기준
- Selenium 페이지 버튼 클릭 방식(crawler.py) 대신 crawler_metadata.py의 목록 파서 재사용.
- currentPage를 직접 1,2,3... 증가시키며 수집해 페이지 그룹 클릭 누락을 방지.
- 결과 컬럼은 기존과 동일하게 유지: 데이터명 / 조회수 / 다운로드수
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd
import requests

import crawler_metadata as cm

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


DEFAULT_PER_PAGE = 1000
DEFAULT_MAX_PAGES = 1000


def make_org_candidates(user_input: str) -> list[str]:
    base = (user_input or "").strip()
    if not base:
        return []
    candidates = [base]
    if "(주)" not in base and "㈜" not in base:
        candidates.extend([base + "(주)", base + "㈜"])
    else:
        candidates.extend([base.replace("(주)", "㈜"), base.replace("㈜", "(주)")])
    if "강원특별자치도" in base:
        candidates.append(base.replace("강원특별자치도", "강원도"))
    if "강원도" in base:
        candidates.append(base.replace("강원도", "강원특별자치도"))
    return list(dict.fromkeys([c for c in candidates if c.strip()]))


def build_org_url(org_name: str, current_page: int = 1, per_page: int = DEFAULT_PER_PAGE) -> str:
    org = (org_name or "").strip()
    params = {
        "dType": "FILE",
        "sort": "updtDt",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "org": org,
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)


def _to_int(value) -> int:
    text = str(value or "")
    m = re.search(r"[0-9][0-9,]*", text)
    if not m:
        return 0
    return int(m.group(0).replace(",", ""))


def collect_stats_by_metadata_parser(org_name: str, per_page: int = DEFAULT_PER_PAGE, max_pages: int = DEFAULT_MAX_PAGES) -> tuple[pd.DataFrame, str]:
    """
    crawler_metadata.py의 collect_dataset_links_from_html()을 활용하여 목록 카드의
    데이터명/조회수/다운로드수를 수집한다.
    """
    headers = cm.build_http_headers()
    rows = []
    seen_urls = set()
    seen_fallback = set()
    used_first_url = build_org_url(org_name, 1, per_page)
    empty_streak = 0

    session = requests.Session()
    session.headers.update(headers)

    for page_no in range(1, max_pages + 1):
        list_url = build_org_url(org_name, page_no, per_page)
        if page_no == 1:
            used_first_url = list_url

        print(f"[LIST] page {page_no:04d} 요청 중 | perPage={per_page}", flush=True)
        try:
            res = session.get(list_url, timeout=30)
            res.raise_for_status()
        except Exception as e:
            print(f"[LIST] page {page_no:04d} 요청 실패: {repr(e)}", flush=True)
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue

        items = cm.collect_dataset_links_from_html(res.text, list_url)
        print(f"[LIST] page {page_no:04d} +{len(items):,}건", flush=True)

        if not items:
            empty_streak += 1
            if empty_streak >= 1:
                break
            continue
        empty_streak = 0

        new_count = 0
        for item in items:
            title = cm.clean_dataset_title(item.get("title") or item.get("raw_title") or "")
            detail_url = item.get("detail_url", "")
            view = item.get("조회수", "")
            download = item.get("다운로드(바로가기)", "") or item.get("다운로드수", "")

            if not title:
                continue

            if detail_url:
                if detail_url in seen_urls:
                    continue
                seen_urls.add(detail_url)
            else:
                fallback_key = (title, str(view), str(download))
                if fallback_key in seen_fallback:
                    continue
                seen_fallback.add(fallback_key)

            rows.append({
                "데이터명": title,
                "조회수": _to_int(view),
                "다운로드수": _to_int(download),
            })
            new_count += 1

        print(f"[LIST] page {page_no:04d} 신규 {new_count:,}건 | 누적 {len(rows):,}건", flush=True)

        # 포털이 마지막 페이지 이후 같은 결과를 반복 반환하는 경우를 방지
        if new_count == 0:
            break

        # perPage=1000 요청인데 실제 반환이 perPage보다 적으면 마지막 페이지로 판단 가능
        if len(items) < per_page:
            print(f"[LIST] page {page_no:04d} 반환 건수({len(items):,})가 perPage({per_page:,})보다 작아 종료합니다.", flush=True)
            break

    df = pd.DataFrame(rows, columns=["데이터명", "조회수", "다운로드수"])
    return df, used_first_url


def main():
    parser = argparse.ArgumentParser(description="기관별 조회수/다운로드 수 수집 wrapper")
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    org_input = args.org_name.strip()
    candidates = make_org_candidates(org_input)

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - stats_runner]", flush=True)
    print(f"- org_name: {org_input}", flush=True)
    print(f"- candidates: {candidates}", flush=True)
    print(f"- per_page: {args.per_page}", flush=True)
    print(f"- max_pages: {args.max_pages}", flush=True)
    print("※ crawler_metadata.py 목록 파서를 활용해 조회수/다운로드 수를 수집합니다.", flush=True)
    print("=" * 80, flush=True)

    last_error = None
    used_org = org_input
    used_url = ""
    df = pd.DataFrame(columns=["데이터명", "조회수", "다운로드수"])

    for idx, org in enumerate(candidates, start=1):
        print(f"\n[기관 후보 {idx}/{len(candidates)}] {org}", flush=True)
        try:
            candidate_df, candidate_url = collect_stats_by_metadata_parser(
                org,
                per_page=max(1, int(args.per_page)),
                max_pages=max(1, int(args.max_pages)),
            )
            used_org = org
            used_url = candidate_url
            if candidate_df is not None and not candidate_df.empty:
                df = candidate_df
                print(f"[성공] {org} 기준 {len(df):,}건 수집", flush=True)
                break
            print(f"[알림] {org} 기준 수집 결과 0건. 다음 후보를 확인합니다.", flush=True)
        except Exception as e:
            last_error = e
            print(f"[경고] {org} 기준 수집 실패: {repr(e)}", flush=True)
            continue

    if df is None or df.empty:
        if last_error is not None:
            raise RuntimeError(f"모든 기관 후보에서 조회수/다운로드 수 수집 실패. 마지막 오류: {repr(last_error)}")
        raise RuntimeError("모든 기관 후보에서 수집 결과가 0건입니다.")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    safe_org_name = used_org.replace("(", "_").replace(")", "")
    excel_path = output_dir / f"공공데이터_{safe_org_name}_조회수_다운로드수_{timestamp}.xlsx"

    with pd.ExcelWriter(excel_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    result = {
        "status": "completed",
        "org_name": used_org,
        "target_url": used_url,
        "row_count": int(len(df)),
        "output_dir": str(output_dir),
        "excel_path": str(excel_path),
    }
    Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[저장 완료] {excel_path}", flush=True)


if __name__ == "__main__":
    main()
