# -*- coding: utf-8 -*-
"""
Streamlit wrapper runner for crawler_metadata.py.

원칙
- crawler_metadata.py의 상세 수집/httpx 파싱/저장 로직은 그대로 사용한다.
- 기관별 수집에서 누락을 줄이기 위해 URL 조건을 단일 org 파라미터에 의존하지 않는다.
- 기관명 후보 + 제공기관 필터 URL + 키워드 URL을 순차 실행한 뒤 상세페이지 URL 기준으로 병합한다.
"""
import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from datetime import datetime

import pandas as pd

import crawler_metadata as cm

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


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


def norm_org_name(value: str) -> str:
    text = str(value or "").strip()
    text = text.replace("㈜", "(주)")
    text = re.sub(r"\s+", "", text)
    return text


def build_provider_target_url(org_name: str, *, current_page: int = 1, per_page: int = 1000) -> str:
    org = (org_name or "").strip()
    params = {
        "dType": "FILE",
        "keyword": "",
        "detailKeyword": "",
        "publicDataPk": "",
        "recmSe": "",
        "detailText": "",
        "relatedKeyword": "",
        "commaNotInData": "",
        "commaAndData": "",
        "commaOrData": "",
        "must_not": "",
        "tabId": "",
        "dataSetCoreTf": "",
        "coreDataNm": "",
        "sort": "updtDt",
        "relRadio": "",
        "orgFullName": org,
        "orgFilter": org,
        "org": org,
        "orgSearch": "",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)


def normalize_copied_list_url(url: str, *, per_page: int = 1000) -> str:
    """
    사용자가 포털에서 복사한 기관별 목록 URL을 그대로 보존하되,
    수집 안정성을 위해 currentPage=1, perPage=1000만 조정합니다.

    중요:
    - org/orgFullName/orgFilter/keyword 등 포털에서 생성한 필터 파라미터는 임의로 만들거나 삭제하지 않습니다.
    - URL을 새로 조립하지 않기 때문에, 실제 포털에서 0건이 아닌 것을 확인한 URL 구조를 그대로 태웁니다.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(query_pairs)
    query["currentPage"] = "1"
    query["perPage"] = str(per_page)
    # 파일데이터 목록 기준은 보장하되, 사용자가 복사한 나머지 필터는 유지
    query.setdefault("dType", "FILE")
    query.setdefault("sort", "updtDt")
    new_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def build_keyword_target_url(keyword: str, *, current_page: int = 1, per_page: int = 1000) -> str:
    # 제공기관 필터 URL이 포털 상황에 따라 0건을 반환할 때를 대비한 보조 회수 URL.
    # 상세 수집 후 제공기관 컬럼 기준으로 다시 필터링하므로 최종 산출물 오염을 막는다.
    kw = (keyword or "").strip()
    params = {
        "dType": "FILE",
        "keyword": kw,
        "detailKeyword": "",
        "publicDataPk": "",
        "recmSe": "",
        "detailText": "",
        "relatedKeyword": "",
        "commaNotInData": "",
        "commaAndData": "",
        "commaOrData": "",
        "must_not": "",
        "tabId": "",
        "dataSetCoreTf": "",
        "coreDataNm": "",
        "sort": "updtDt",
        "relRadio": "",
        "orgFullName": "",
        "orgFilter": "",
        "org": "",
        "orgSearch": "",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)


def make_org_target_jobs(org_input: str, per_page: int) -> list[dict]:
    jobs = []
    seen = set()
    for org in make_org_candidates(org_input):
        for mode, url in [
            ("provider", build_provider_target_url(org, per_page=per_page)),
            ("keyword", build_keyword_target_url(org, per_page=per_page)),
        ]:
            key = (mode, org)
            if key in seen:
                continue
            seen.add(key)
            jobs.append({"mode": mode, "org": org, "url": url})
    return jobs


def read_excel_if_exists(path: Path) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_excel(path)
        except Exception as e:
            print(f"[경고] 엑셀 읽기 실패: {path} / {repr(e)}", flush=True)
    return pd.DataFrame()


def filter_metadata_by_provider(df: pd.DataFrame, allowed_orgs: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=cm.TARGET_METADATA_COLUMNS)
    if "제공기관" not in df.columns:
        return df.reindex(columns=cm.TARGET_METADATA_COLUMNS)

    allowed_norms = {norm_org_name(x) for x in allowed_orgs if str(x).strip()}
    if not allowed_norms:
        return df.reindex(columns=cm.TARGET_METADATA_COLUMNS)

    provider = df["제공기관"].fillna("").astype(str).map(norm_org_name)
    mask = provider.isin(allowed_norms)

    # 제공기관이 비어 있는 행은 정상 상세 수집으로 보기 어렵다. 최종 성공 rows에서는 제외한다.
    filtered = df[mask].copy()
    return filtered.reindex(columns=cm.TARGET_METADATA_COLUMNS)


def merge_candidate_outputs(output_dir: Path, candidate_dirs: list[Path], allowed_orgs: list[str]):
    meta_frames = []
    fail_frames = []

    for d in candidate_dirs:
        meta_path = d / "메타데이터.xlsx"
        fail_path = d / "실패로그.xlsx"
        meta_df = read_excel_if_exists(meta_path)
        fail_df = read_excel_if_exists(fail_path)
        if not meta_df.empty:
            meta_df["수집파일"] = meta_df.get("수집파일", "")
            meta_frames.append(meta_df)
        if not fail_df.empty:
            fail_frames.append(fail_df)

    if meta_frames:
        merged_meta = pd.concat(meta_frames, ignore_index=True)
    else:
        merged_meta = pd.DataFrame(columns=cm.TARGET_METADATA_COLUMNS)

    merged_meta = filter_metadata_by_provider(merged_meta, allowed_orgs)

    if not merged_meta.empty and "상세페이지 URL" in merged_meta.columns:
        merged_meta["상세페이지 URL"] = merged_meta["상세페이지 URL"].fillna("").astype(str)
        merged_meta = merged_meta.drop_duplicates(subset=["상세페이지 URL"], keep="first")

    if not merged_meta.empty:
        merged_meta = merged_meta.reset_index(drop=True)
        merged_meta["최종순번"] = range(1, len(merged_meta) + 1)

    merged_meta = merged_meta.reindex(columns=cm.TARGET_METADATA_COLUMNS)

    if fail_frames:
        merged_fail = pd.concat(fail_frames, ignore_index=True)
        if "URL" in merged_fail.columns:
            success_urls = set(merged_meta.get("상세페이지 URL", pd.Series(dtype=str)).dropna().astype(str))
            merged_fail = merged_fail[~merged_fail["URL"].astype(str).isin(success_urls)].copy()
            merged_fail = merged_fail.drop_duplicates(subset=["URL"], keep="last")
    else:
        merged_fail = pd.DataFrame(columns=cm.FAIL_COLUMNS)

    merged_fail = merged_fail.reindex(columns=cm.FAIL_COLUMNS)

    output_dir.mkdir(parents=True, exist_ok=True)
    cm.write_excel_no_url_warning(merged_meta, output_dir / "메타데이터.xlsx", sheet_name="메타데이터")
    cm.write_excel_no_url_warning(merged_fail, output_dir / "실패로그.xlsx", sheet_name="실패로그")

    print("\n[기관별 병합 저장 완료]", flush=True)
    print(f"- 메타데이터: {output_dir / '메타데이터.xlsx'}", flush=True)
    print(f"- 실패로그: {output_dir / '실패로그.xlsx'}", flush=True)
    print(f"- 최종 성공 rows: {len(merged_meta):,}", flush=True)
    print(f"- 최종 실패 rows: {len(merged_fail):,}", flush=True)

    return merged_meta, merged_fail


def run_cm_once(*, target_url: str, job_name: str, output_dir: Path, run_mode: str, max_pages: int, max_items: int, list_per_page: int, concurrency: int | None):
    output_dir.mkdir(parents=True, exist_ok=True)

    cm.RUN_MODE = run_mode
    cm.JOB_NAME = job_name
    cm.TARGET_URL = target_url
    cm.OUTPUT_DIR = str(output_dir)
    cm.MAX_PAGES = int(max_pages)
    cm.MAX_DETAIL_ITEMS = int(max_items)
    cm.LIST_PER_PAGE = int(list_per_page)
    if concurrency is not None:
        cm.DETAIL_CONCURRENCY = int(concurrency)

    cm.RETRY_FAIL_LOG_PATH = None
    cm.RETRY_EXISTING_METADATA_PATH = None
    cm.RETRY_EXISTING_COLUMNS_PATH = None

    print("\n" + "-" * 80, flush=True)
    print(f"[candidate 실행] {job_name}", flush=True)
    print(f"- target_url: {target_url}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print("-" * 80, flush=True)

    # crawler_metadata.py 원본 실행 흐름 사용
    cm.main()


def main():
    parser = argparse.ArgumentParser(description="공공데이터포털 메타데이터 크롤러 wrapper")
    parser.add_argument("--scope", choices=["all", "org"], required=True)
    parser.add_argument("--org-name", default="")
    parser.add_argument("--target-url", default="", help="포털에서 복사한 기관별 파일데이터 목록 URL. 제공되면 이 URL 1개만 실행합니다.")
    parser.add_argument("--run-mode", choices=["MAIN", "BOTH", "RETRY_FAILED"], default="MAIN")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--list-per-page", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status = "unknown"

    print("=" * 80, flush=True)
    print("[Streamlit wrapper - metadata_runner]", flush=True)
    print(f"- scope: {args.scope}", flush=True)
    print(f"- org_name: {args.org_name}", flush=True)
    print(f"- target_url: {args.target_url}", flush=True)
    print(f"- run_mode: {args.run_mode}", flush=True)
    print(f"- output_dir: {output_dir}", flush=True)
    print(f"- max_pages: {args.max_pages}", flush=True)
    print(f"- max_items: {args.max_items}", flush=True)
    print(f"- list_per_page: {args.list_per_page}", flush=True)
    print("※ crawler_metadata.py 수집 엔진을 실행합니다.", flush=True)
    print("=" * 80, flush=True)

    try:
        if args.scope == "all":
            max_pages = cm.MAX_PAGES if args.max_pages is None else args.max_pages
            max_items = cm.MAX_DETAIL_ITEMS if args.max_items is None else args.max_items
            run_cm_once(
                target_url=cm.TARGET_URL,
                job_name="공공데이터포털_전체_메타데이터",
                output_dir=output_dir,
                run_mode=args.run_mode,
                max_pages=max_pages,
                max_items=max_items,
                list_per_page=args.list_per_page,
                concurrency=args.concurrency,
            )
        else:
            if not args.org_name.strip():
                raise ValueError("기관별 수집에는 --org-name이 필요합니다.")

            org_input = args.org_name.strip()
            max_pages = 0 if args.max_pages is None else args.max_pages
            max_items = 0 if args.max_items is None else args.max_items

            # 기관별 메타데이터는 사용자가 포털에서 실제 확인한 URL을 그대로 실행한다.
            # URL 후보/키워드 후보/후처리 필터는 기관별 0건 URL 문제를 만들 수 있어 사용하지 않는다.
            target_url = normalize_copied_list_url(args.target_url, per_page=args.list_per_page)
            if not target_url:
                # fallback은 유지하되, 이 경우는 참고용 자동 생성 URL이므로 로그에 명확히 남긴다.
                target_url = build_provider_target_url(org_input, per_page=args.list_per_page)
                print("[경고] --target-url이 없어 자동 생성 URL을 사용합니다. 포털에서 0건이면 실제 제공기관 URL을 복사해 입력하세요.", flush=True)

            print("[기관별 단일 URL 실행]", flush=True)
            print(f"- 입력 기관명: {org_input}", flush=True)
            print(f"- 실행 URL: {target_url}", flush=True)
            print("- URL 후보 생성/키워드 검색/제공기관 후처리 필터링 없음", flush=True)

            run_cm_once(
                target_url=target_url,
                job_name=f"공공데이터_{org_input}_메타데이터",
                output_dir=output_dir,
                run_mode=args.run_mode,
                max_pages=max_pages,
                max_items=max_items,
                list_per_page=args.list_per_page,
                concurrency=args.concurrency,
            )

        status = "completed"
    except KeyboardInterrupt:
        status = "stopped"
        print("\n[중지] 사용자 요청으로 중지되었습니다.", flush=True)
    except Exception as e:
        status = "error"
        print(f"\n[오류] metadata_runner 실행 실패: {repr(e)}", flush=True)
        raise
    finally:
        result = {
            "status": status,
            "scope": args.scope,
            "org_name": args.org_name,
            "started_at": started_at,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "output_dir": str(output_dir),
            "metadata_path": str(output_dir / "메타데이터.xlsx"),
            "fail_path": str(output_dir / "실패로그.xlsx"),
        }
        Path(args.result_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
