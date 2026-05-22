# -*- coding: utf-8 -*-
"""
공공데이터포털 제공기관명/기관별 목록 URL 해석 유틸 v3.

수정 핵심
- (주), (재), (BAC) 같은 괄호 안 문자열을 절대 하드코딩하지 않는다.
- 사용자가 입력한 값을 org/orgFullName/orgFilter에 바로 넣지 않는다.
- 먼저 keyword 기반 넓은 검색 결과를 보되, 목록명 prefix가 입력값과 맞는 카드에서만 실제 제공기관명 원문을 추출한다.
- 괄호 제거는 비교용 정규화에만 사용한다.
- 최종 수집 URL에는 포털에 실제 표시된 제공기관명 원문을 넣는다.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import requests
from bs4 import BeautifulSoup

BASE_LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.data.go.kr/",
}

FILE_TYPE_PATTERN = (
    r"CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|"
    r"MP4|AVI|MOV|WMV|JPG|JPEG|PNG|GIF|DOCX|DOC|PPTX|PPT|"
    r"파일데이터|오픈API|API"
)


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_org_for_match(name: str) -> str:
    """
    기관명 비교용 정규화.

    예시
    - (재)서울문화재단 -> 서울문화재단
    - 한국중부발전(주) -> 한국중부발전
    - 기관명(BAC) -> 기관명

    주의: 최종 URL에는 이 값을 쓰지 않는다. 비교에만 사용한다.
    """
    s = clean_text(name)
    if not s:
        return ""

    s = s.replace("（", "(").replace("）", ")")

    # 앞쪽 괄호 블록 반복 제거: (재)(사)@@@@ -> @@@@
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"^\s*\([^)]*\)\s*", "", s)

    # 뒤쪽 괄호 블록 반복 제거: @@@@(주)(본부) -> @@@@
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"\s*\([^)]*\)\s*$", "", s)

    # 비교용으로만 구분기호 제거
    s = re.sub(r"[\s·ㆍ_\-]+", "", s)
    return s.lower()


def _base_params(current_page: int = 1, per_page: int = 10) -> dict:
    return {
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


def _make_url(params: dict) -> str:
    return BASE_LIST_URL + "?" + urllib.parse.urlencode(params)


def build_org_filter_url(org_name: str, *, current_page: int = 1, per_page: int = 10) -> str:
    """실제 제공기관명 원문을 orgFullName/orgFilter/org에 넣은 최종 수집 URL."""
    org = clean_text(org_name)
    params = _base_params(current_page=current_page, per_page=per_page)
    params["orgFullName"] = org
    params["orgFilter"] = org
    params["org"] = org
    return _make_url(params)


def build_simple_org_url(org_name: str, *, current_page: int = 1, per_page: int = 10) -> str:
    """구버전 호환용 org 단독 URL."""
    org = clean_text(org_name)
    params = {
        "dType": "FILE",
        "sort": "updtDt",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "org": org,
    }
    return _make_url(params)


def build_keyword_url(keyword: str, *, current_page: int = 1, per_page: int = 10) -> str:
    """제공기관명 원문을 찾기 위한 넓은 검색 URL."""
    q = clean_text(keyword)
    params = _base_params(current_page=current_page, per_page=per_page)
    params["keyword"] = q
    return _make_url(params)


def build_org_search_url(keyword: str, *, current_page: int = 1, per_page: int = 10) -> str:
    q = clean_text(keyword)
    params = _base_params(current_page=current_page, per_page=per_page)
    params["orgSearch"] = q
    return _make_url(params)


def build_org_search_probe_urls(user_input: str, *, current_page: int = 1, per_page: int = 10) -> list[str]:
    """
    실제 제공기관명을 찾기 위한 확인 URL 후보.

    중요:
    - 정확하지 않은 org 필터는 0건을 만드는 경우가 많다.
    - keyword 검색 결과에는 설명/키워드에 입력값이 포함된 타기관 데이터도 섞인다.
    - 따라서 이후 후보 추출 단계에서 반드시 "목록명 prefix"가 입력값과 맞는 카드만 사용한다.
    """
    q = clean_text(user_input)
    if not q:
        return []

    urls: list[str] = []

    # keyword 검색은 설명/키워드 매칭 때문에 타기관 데이터가 섞일 수 있으므로
    # 1~3페이지만 빠르게 확인하고, 후보 채택은 목록명 prefix 기준으로만 한다.
    for page_no in [current_page, current_page + 1, current_page + 2]:
        urls.append(build_keyword_url(q, current_page=page_no, per_page=per_page))

    # orgSearch/org 필터는 보조 확인용이다. 정확 기관명 확정은 목록명 기준 후보에서 우선 수행한다.
    urls.extend([
        build_org_search_url(q, current_page=current_page, per_page=per_page),
        build_org_filter_url(q, current_page=current_page, per_page=per_page),
        build_simple_org_url(q, current_page=current_page, per_page=per_page),
    ])
    return list(dict.fromkeys(urls))


def strip_list_badges(title: str) -> str:
    s = clean_text(title)
    if not s:
        return ""
    s = re.sub(r"미리보기", "", s)
    s = re.sub(rf"^((?:{FILE_TYPE_PATTERN})\s*(?:\+|,|/|\\|｜|·|ㆍ|-)?\s*)+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(New|Update|Updated|NEW|UPDATE|업데이트)\b", "", s, flags=re.IGNORECASE)
    cut_markers = ["제공기관", "분류체계", "수정일", "등록일", "조회수", "다운로드", "키워드"]
    cut_positions = [s.find(m) for m in cut_markers if s.find(m) > 0]
    if cut_positions:
        s = s[:min(cut_positions)]
    return clean_text(s)


def org_prefix_from_dataset_title(title: str) -> str:
    """
    데이터명 앞 기관명 prefix 추출.
    예: 'CSV JSON + XML 한국중부발전(주)_발전실적 정보' -> '한국중부발전(주)'
    """
    s = strip_list_badges(title)
    if not s or "_" not in s:
        return ""
    prefix = clean_text(s.split("_", 1)[0])
    # 너무 일반적인 메뉴/짧은 값 제거
    if len(normalize_org_for_match(prefix)) < 2:
        return ""
    return prefix


def get_dataset_title_from_card(li) -> str:
    """목록 카드에서 파일데이터명 텍스트만 최대한 추출한다."""
    title_candidates: list[str] = []

    # 공공데이터포털 목록 카드의 제목은 보통 상세 URL을 가진 a 또는 .title에 있다.
    for selector in [
        "a[href*='/data/'][href*='fileData.do']",
        "a[href*='/dataset/'][href*='fileData.do']",
        "a[href*='/data/']",
        "a[href*='/dataset/']",
        "span.title",
        ".title",
    ]:
        for el in li.select(selector):
            txt = strip_list_badges(el.get_text(" "))
            if txt:
                title_candidates.append(txt)

    # selector가 안 잡히면 카드 전체 텍스트를 마지막 후보로 사용한다.
    if not title_candidates:
        title_candidates.append(strip_list_badges(li.get_text(" ")))

    for title in title_candidates:
        if title and "_" in title:
            return title
    return title_candidates[0] if title_candidates else ""


def title_prefix_matches_input(dataset_title: str, user_input: str) -> bool:
    """
    목록명 prefix가 사용자 입력 기관명과 맞는지 확인한다.

    예: 입력 '한국중부발전'
    - '한국중부발전(주)_발전실적 정보' => True
    - '한국동서발전(주)_발전5사 ... 한국중부발전 ...' => False
    """
    n_input = normalize_org_for_match(user_input)
    if not n_input:
        return False

    prefix = org_prefix_from_dataset_title(dataset_title)
    if not prefix:
        return False

    n_prefix = normalize_org_for_match(prefix)
    return n_prefix == n_input or n_prefix.startswith(n_input) or n_input.startswith(n_prefix)


def extract_value_from_text_by_label(text: str, label: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    stop_labels = [
        "제공기관", "분류체계", "등록일", "수정일", "조회수", "조회 수",
        "다운로드", "다운로드수", "다운로드 수", "키워드", "주기성 데이터",
        "관리부서명", "관리부서 전화번호", "업데이트 주기", "제공형태",
    ]
    others = [re.escape(x) for x in stop_labels if x != label]
    stop = "|".join(others)
    pat = rf"{re.escape(label)}\s*[:：]?\s*(.*?)(?=\s*(?:{stop})\s*[:：]?|$)"
    m = re.search(pat, text)
    if not m:
        return ""
    value = clean_text(m.group(1))
    return value if 0 < len(value) <= 200 else ""


def _add_candidate(candidates: list[str], value: str):
    v = clean_text(value)
    if not v:
        return
    bad = {"파일데이터", "오픈 API", "오픈API", "데이터목록", "조건검색", "검색"}
    if v in bad:
        return
    if v not in candidates:
        candidates.append(v)


def extract_org_names_from_list_html(html: str, user_input: str = "") -> list[str]:
    """
    목록 HTML에서 실제 제공기관명 후보를 추출한다.

    v3 원칙:
    - keyword 검색 결과에는 설명/키워드에 입력값이 포함된 타기관 데이터가 섞인다.
    - 따라서 후보 채택은 반드시 "목록명 prefix" 기준으로만 한다.
    - 목록명이 입력기관과 맞는 카드에서만 '제공기관' 라벨 값을 후보로 쓴다.
    - 목록명이 맞지 않는 카드의 제공기관/설명/키워드 값은 절대 후보로 사용하지 않는다.
    """
    soup = BeautifulSoup(html or "", "lxml")
    org_names: list[str] = []
    n_input = normalize_org_for_match(user_input)

    selectors = [
        "div.result-list ul li",
        "#fileDataList ul li",
        "ul.result-list li",
        "ul.data-list li",
    ]

    for li in soup.select(", ".join(selectors)):
        title = get_dataset_title_from_card(li)

        # 사용자 입력이 있으면 목록명 prefix가 맞는 카드만 사용한다.
        if n_input and not title_prefix_matches_input(title, user_input):
            continue

        # 1순위: 목록명이 맞는 카드의 제공기관 라벨
        text = clean_text(li.get_text(" "))
        org = extract_value_from_text_by_label(text, "제공기관")
        _add_candidate(org_names, org)

        # 2순위: 목록명 prefix 자체
        prefix = org_prefix_from_dataset_title(title)
        _add_candidate(org_names, prefix)

    # li 구조가 전혀 안 잡히는 특수 HTML일 때만 링크 제목 prefix fallback을 사용한다.
    # 이 경우에도 prefix가 사용자 입력과 맞는 링크만 후보로 채택한다.
    if not org_names:
        for a in soup.select("a"):
            title = strip_list_badges(a.get_text(" "))
            if n_input and not title_prefix_matches_input(title, user_input):
                continue
            prefix = org_prefix_from_dataset_title(title)
            _add_candidate(org_names, prefix)

    return list(dict.fromkeys(org_names))

def has_dataset_items(html: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
    text = clean_text(soup.get_text(" "))
    if "파일데이터 (0건)" in text or "파일데이터(0건)" in text:
        return False
    return bool(
        soup.select(
            "div.result-list ul li, "
            "#fileDataList ul li, "
            "ul.result-list li, "
            "ul.data-list li, "
            "a[href*='/data/'][href*='fileData.do'], "
            "a[href*='/dataset/'][href*='fileData.do']"
        )
    )


def _file_count_from_html(html: str) -> int | None:
    text = clean_text(BeautifulSoup(html or "", "lxml").get_text(" "))
    m = re.search(r"파일데이터\s*\(\s*([0-9,]+)\s*건\s*\)", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def _fetch_probe(url: str, headers: dict | None, timeout: int, user_input: str = "") -> dict:
    try:
        res = requests.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
        res.raise_for_status()
        html = res.text
        org_names = extract_org_names_from_list_html(html, user_input=user_input)
        file_count = _file_count_from_html(html)
        return {
            "ok": True,
            "url": url,
            "html": html,
            "org_names": org_names,
            "has_items": has_dataset_items(html),
            "file_count": file_count,
            "error": "",
        }
    except Exception as e:
        return {
            "ok": False,
            "url": url,
            "html": "",
            "org_names": [],
            "has_items": False,
            "file_count": None,
            "error": repr(e),
        }


def url_has_dataset_items(url: str, *, headers: dict | None = None, timeout: int = 5) -> bool:
    if not clean_text(url):
        return False
    r = _fetch_probe(url, headers=headers, timeout=timeout)
    return bool(r.get("has_items"))


def _score_org_candidate(user_input: str, org_name: str, count: int = 1) -> tuple[int, str]:
    raw = clean_text(user_input)
    org = clean_text(org_name)
    n_input = normalize_org_for_match(raw)
    n_org = normalize_org_for_match(org)

    if not n_input or not n_org:
        return 0, org

    score = 0
    if n_org == n_input:
        score = 100
    elif n_org.startswith(n_input) or n_input.startswith(n_org):
        score = 80
    elif n_input in n_org or n_org in n_input:
        score = 60

    # 같은 기관명이 여러 카드에서 반복되면 가산
    score += min(count, 20)

    # 정규화 후 입력보다 너무 긴 후보는 약간 감점
    score -= max(0, len(n_org) - len(n_input)) // 4
    return score, org


def resolve_org_name_and_url_fast(
    user_input: str,
    *,
    headers: dict | None = None,
    timeout: int = 3,
    per_page: int = 10,
    max_workers: int = 4,
) -> dict:
    """
    사용자 입력값으로 실제 제공기관명과 수집 URL을 빠르게 확정한다.

    반환 dict:
    - found: 실제 후보를 찾았는지 여부
    - exact_org: 최종 선택 기관명. found=False이면 원 입력값
    - candidates: 정규화 기준으로 매칭된 실제 제공기관명 후보
    - url: exact_org 기준 최종 기관별 파일데이터 URL. found=False이면 keyword 검색 URL
    - source_url: 후보를 찾은 검색 URL
    - item_count: 확인된 파일데이터 건수 또는 후보 카운트
    - probe_results: 디버깅용 요약
    """
    raw = clean_text(user_input)
    if not raw:
        return {
            "found": False,
            "exact_org": "",
            "candidates": [],
            "url": "",
            "source_url": "",
            "item_count": 0,
            "probe_results": [],
        }

    probe_urls = build_org_search_probe_urls(raw, current_page=1, per_page=per_page)
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(probe_urls)))) as ex:
        futures = [ex.submit(_fetch_probe, url, headers, timeout, raw) for url in probe_urls]
        for fut in as_completed(futures):
            results.append(fut.result())

    org_counter: Counter[str] = Counter()
    first_source_by_org: dict[str, str] = {}
    first_success_url = ""
    first_items_url = ""
    first_items_count = 0

    for r in results:
        if r.get("ok") and not first_success_url:
            first_success_url = r.get("url", "")
        if r.get("has_items") and not first_items_url:
            first_items_url = r.get("url", "")
            first_items_count = r.get("file_count") or 1
        for org in r.get("org_names", []):
            org_counter[org] += 1
            first_source_by_org.setdefault(org, r.get("url", ""))

    scored = []
    for org, cnt in org_counter.items():
        score, _ = _score_org_candidate(raw, org, cnt)
        if score >= 60:
            scored.append((score, org))

    scored.sort(key=lambda x: (-x[0], len(normalize_org_for_match(x[1])), x[1]))
    candidates = [org for _, org in scored]

    probe_summary = [
        {
            "ok": r.get("ok"),
            "has_items": r.get("has_items"),
            "file_count": r.get("file_count"),
            "org_names": r.get("org_names", [])[:5],
            "url": r.get("url", ""),
            "error": r.get("error", ""),
        }
        for r in results
    ]

    if candidates:
        exact_org = candidates[0]
        final_url = build_org_filter_url(exact_org, current_page=1, per_page=per_page)
        return {
            "found": True,
            "exact_org": exact_org,
            "candidates": candidates,
            "url": final_url,
            "source_url": first_source_by_org.get(exact_org, first_items_url or first_success_url),
            "item_count": int(max(first_items_count, sum(org_counter.values()))),
            "probe_results": probe_summary,
        }

    # 후보를 못 찾은 경우 raw org filter URL을 보여주면 0건이 되는 문제가 있어,
    # 최소한 데이터가 있는 검색 URL을 반환한다. 단, found=False로 표시한다.
    fallback_url = first_items_url or build_keyword_url(raw, current_page=1, per_page=per_page)
    return {
        "found": False,
        "exact_org": raw,
        "candidates": [],
        "url": fallback_url,
        "source_url": first_items_url or first_success_url,
        "item_count": int(first_items_count or 0),
        "probe_results": probe_summary,
    }


def pick_url_candidates_for_collection(org_name: str, *, resolved_url: str = "", per_page: int = 1000) -> list[str]:
    """수집 runner에서 쓸 fallback URL 후보. 괄호값 접미어를 임의 생성하지 않는다."""
    org = clean_text(org_name)
    urls: list[str] = []
    if resolved_url:
        urls.append(resolved_url)
    if org:
        urls.append(build_org_filter_url(org, current_page=1, per_page=per_page))
        urls.append(build_simple_org_url(org, current_page=1, per_page=per_page))
        urls.append(build_keyword_url(org, current_page=1, per_page=per_page))
    return list(dict.fromkeys(urls))
