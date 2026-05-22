# -*- coding: utf-8 -*-
"""
공공데이터포털 제공기관명/기관별 목록 URL 해석 유틸.

핵심 원칙
- (주), (재), (BAC) 같은 괄호 안 문자열을 하드코딩하지 않는다.
- 사용자가 입력한 기관명을 바로 org/orgFullName/orgFilter에 넣기 전에,
  포털 목록 HTML에서 실제 '제공기관' 값을 추출한다.
- 괄호 제거는 비교용 정규화에만 사용하고, 최종 URL에는 실제 제공기관명 원문을 넣는다.
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
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


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

    # 전각 괄호를 일반 괄호로 통일
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

    # 비교 편의용 공백/구분기호 정리
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


def build_org_search_probe_urls(user_input: str, *, current_page: int = 1, per_page: int = 10) -> list[str]:
    """
    실제 제공기관명을 찾기 위한 빠른 확인 URL 후보.
    특정 괄호값 후보를 만들지 않고, 입력값으로 포털을 넓게 검색한다.
    """
    q = clean_text(user_input)
    if not q:
        return []

    urls: list[str] = []

    # 1) 사용자가 정확히 입력했을 수도 있으므로 full org filter 먼저 확인
    urls.append(build_org_filter_url(q, current_page=current_page, per_page=per_page))

    # 2) 기존 org 단독 방식도 확인
    urls.append(build_simple_org_url(q, current_page=current_page, per_page=per_page))

    # 3) keyword 검색으로 실제 목록 카드의 제공기관명을 추출
    p_keyword = _base_params(current_page=current_page, per_page=per_page)
    p_keyword["keyword"] = q
    urls.append(_make_url(p_keyword))

    # 4) orgSearch 필드도 보조 확인
    p_org_search = _base_params(current_page=current_page, per_page=per_page)
    p_org_search["orgSearch"] = q
    urls.append(_make_url(p_org_search))

    return list(dict.fromkeys(urls))


def extract_value_from_text_by_label(text: str, label: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    stop_labels = [
        "제공기관", "분류체계", "등록일", "수정일", "조회수", "조회 수",
        "다운로드", "다운로드수", "다운로드 수", "키워드", "관리부서명",
        "관리부서 전화번호", "업데이트 주기", "제공형태",
    ]
    others = [re.escape(x) for x in stop_labels if x != label]
    stop = "|".join(others)
    pat = rf"{re.escape(label)}\s*[:：]?\s*(.*?)(?=\s*(?:{stop})\s*[:：]?|$)"
    m = re.search(pat, text)
    if not m:
        return ""
    value = clean_text(m.group(1))
    return value if len(value) <= 200 else ""


def extract_org_names_from_list_html(html: str) -> list[str]:
    """목록 HTML 카드에서 실제 '제공기관' 값을 추출한다."""
    soup = BeautifulSoup(html or "", "lxml")
    org_names: list[str] = []

    selectors = [
        "div.result-list ul li",
        "#fileDataList ul li",
        "ul.result-list li",
        "ul.data-list li",
    ]

    for li in soup.select(", ".join(selectors)):
        text = clean_text(li.get_text(" "))
        org = extract_value_from_text_by_label(text, "제공기관")
        if org and org not in org_names:
            org_names.append(org)

    return org_names


def has_dataset_items(html: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
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


def _fetch_probe(url: str, headers: dict | None, timeout: int) -> dict:
    try:
        res = requests.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
        res.raise_for_status()
        html = res.text
        return {
            "ok": True,
            "url": url,
            "html": html,
            "org_names": extract_org_names_from_list_html(html),
            "has_items": has_dataset_items(html),
            "error": "",
        }
    except Exception as e:
        return {"ok": False, "url": url, "html": "", "org_names": [], "has_items": False, "error": repr(e)}


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

    # 같은 기관명이 여러 카드에서 반복되면 조금 가산
    score += min(count, 10)

    # 너무 긴 포함 후보보다 짧고 정확한 후보 우선
    score -= max(0, len(n_org) - len(n_input)) // 5
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
    - url: exact_org 기준 최종 기관별 파일데이터 URL
    - source_url: 후보를 찾은 검색 URL
    - item_count: 확인된 목록 존재 여부/개수 보조값
    """
    raw = clean_text(user_input)
    if not raw:
        return {"found": False, "exact_org": "", "candidates": [], "url": "", "source_url": "", "item_count": 0}

    probe_urls = build_org_search_probe_urls(raw, current_page=1, per_page=per_page)
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(probe_urls)))) as ex:
        futures = [ex.submit(_fetch_probe, url, headers, timeout) for url in probe_urls]
        for fut in as_completed(futures):
            results.append(fut.result())

    org_counter: Counter[str] = Counter()
    first_source_by_org: dict[str, str] = {}
    any_success_url = ""
    any_has_items = False

    for r in results:
        if r.get("ok") and not any_success_url:
            any_success_url = r.get("url", "")
        if r.get("has_items"):
            any_has_items = True
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

    if candidates:
        exact_org = candidates[0]
        return {
            "found": True,
            "exact_org": exact_org,
            "candidates": candidates,
            "url": build_org_filter_url(exact_org, current_page=1, per_page=per_page),
            "source_url": first_source_by_org.get(exact_org, any_success_url),
            "item_count": int(sum(org_counter.values())),
        }

    # 실제 제공기관명을 못 찾았지만 목록 자체가 뜬 경우: 원 입력값 URL을 그대로 사용할 수 있게 반환
    return {
        "found": bool(any_has_items),
        "exact_org": raw,
        "candidates": [],
        "url": build_org_filter_url(raw, current_page=1, per_page=per_page),
        "source_url": any_success_url,
        "item_count": 1 if any_has_items else 0,
    }


def pick_url_candidates_for_collection(org_name: str, *, resolved_url: str = "", per_page: int = 1000) -> list[str]:
    """수집 runner에서 쓸 fallback URL 후보. 괄호값 접미어를 임의 생성하지 않는다."""
    org = clean_text(org_name)
    urls = []
    if resolved_url:
        urls.append(resolved_url)
    if org:
        urls.append(build_org_filter_url(org, current_page=1, per_page=per_page))
        urls.append(build_simple_org_url(org, current_page=1, per_page=per_page))
    return list(dict.fromkeys(urls))
