# -*- coding: utf-8 -*-
"""
공공데이터포털 제공기관명/기관별 목록 URL 해석 유틸 v5.

핵심 원칙
- (주), (재), (BAC) 등 괄호 안 문자열을 절대 하드코딩하지 않는다.
- 사용자 입력값을 org/orgFullName/orgFilter에 바로 넣어 0건 URL을 만들지 않는다.
- 먼저 포털 목록에서 실제 제공기관명 원문을 찾는다.
- 후보 채택은 반드시 "목록명 prefix" 기준으로 한다.
  예: 입력 '한국중부발전'일 때
      한국중부발전(주)_...  => 채택
      한국동서발전(주)_... 설명에 한국중부발전 포함 => 제외
- 최종 기관 필터 URL이 0건이면 keyword/orgSearch URL을 fallback으로 쓸 수 있지만,
  이 경우 runner에서 반드시 목록명 prefix 필터를 걸어야 한다.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

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

STOP_LABELS = [
    "제공기관", "분류체계", "등록일", "수정일", "조회수", "조회 수",
    "다운로드", "다운로드수", "다운로드 수", "키워드", "주기성 데이터",
    "관리부서명", "관리부서 전화번호", "업데이트 주기", "제공형태",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_org_for_match(name: str) -> str:
    """
    기관명 비교용 정규화.

    예:
    - (재)서울문화재단 -> 서울문화재단
    - 한국중부발전(주) -> 한국중부발전
    - 기관명(BAC) -> 기관명

    주의: 이 값은 비교용으로만 사용한다. 최종 URL에는 실제 제공기관명 원문을 사용한다.
    """
    s = clean_text(name)
    if not s:
        return ""

    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("㈜", "(주)")

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


def _base_params(current_page: int = 1, per_page: int = 10) -> dict[str, str]:
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


def _make_url(params: dict[str, str]) -> str:
    return BASE_LIST_URL + "?" + urllib.parse.urlencode(params)


def update_list_url(url: str, *, current_page: int | None = None, per_page: int | None = None) -> str:
    if not clean_text(url):
        return ""
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    if current_page is not None:
        q["currentPage"] = str(current_page)
    if per_page is not None:
        q["perPage"] = str(per_page)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))


def build_org_filter_url(org_name: str, *, current_page: int = 1, per_page: int = 10) -> str:
    """실제 제공기관명 원문을 orgFullName/orgFilter/org에 넣은 기관 필터 URL."""
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


def is_keyword_url(url: str) -> bool:
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
        return bool(clean_text((q.get("keyword") or [""])[0]))
    except Exception:
        return False


def is_org_search_url(url: str) -> bool:
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
        return bool(clean_text((q.get("orgSearch") or [""])[0]))
    except Exception:
        return False


def is_exact_org_filter_url(url: str) -> bool:
    """keyword 검색 URL이 아니라 org/orgFullName/orgFilter가 들어간 기관 필터 URL인지 확인한다."""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
        org_values: list[str] = []
        for key in ["orgFullName", "orgFilter", "org"]:
            org_values.extend([clean_text(v) for v in q.get(key, []) if clean_text(v)])
        keyword = clean_text((q.get("keyword") or [""])[0])
        return bool(org_values) and not keyword
    except Exception:
        return False


def get_org_from_filter_url(url: str) -> str:
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url or "").query)
        for key in ["orgFullName", "orgFilter", "org"]:
            value = clean_text((q.get(key) or [""])[0])
            if value:
                return value
    except Exception:
        pass
    return ""


def build_org_search_probe_urls(user_input: str, *, current_page: int = 1, per_page: int = 10) -> list[str]:
    """
    실제 제공기관명을 찾기 위한 확인 URL 후보.

    keyword/orgSearch는 타기관 데이터가 섞일 수 있으므로, 후보 채택은 목록명 prefix 기준으로만 한다.
    """
    q = clean_text(user_input)
    if not q:
        return []

    urls: list[str] = []
    # keyword 검색은 1~3페이지까지 확인. 설명/키워드 매칭 데이터가 섞일 수 있으므로 후보 채택은 엄격히 한다.
    for page_no in [current_page, current_page + 1, current_page + 2]:
        urls.append(build_keyword_url(q, current_page=page_no, per_page=per_page))

    # orgSearch/org 필터는 보조 확인용.
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
    s = re.sub(
        rf"^((?:{FILE_TYPE_PATTERN})\s*(?:\+|,|/|\\|｜|·|ㆍ|-)?\s*)+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"\b(New|Update|Updated|NEW|UPDATE|업데이트)\b", "", s, flags=re.IGNORECASE)
    cut_positions = [s.find(m) for m in STOP_LABELS if s.find(m) > 0]
    if cut_positions:
        s = s[:min(cut_positions)]
    return clean_text(s)


def org_prefix_from_dataset_title(title: str) -> str:
    """
    데이터명 앞 기관명 prefix 추출.
    예: 'CSV JSON + XML 한국중부발전(주)_발전실적 정보' -> '한국중부발전(주)'
    """
    s = strip_list_badges(title)
    if not s:
        return ""

    # 대부분의 공공데이터포털 목록명은 '기관명_데이터명' 형태다.
    if "_" in s:
        prefix = clean_text(s.split("_", 1)[0])
    else:
        # fallback: 공백 전 prefix. 단 너무 긴 제목 전체가 후보가 되지 않도록 보수적으로 사용한다.
        prefix = clean_text(s.split(" ", 1)[0])

    if len(normalize_org_for_match(prefix)) < 2:
        return ""
    return prefix


def get_dataset_title_from_card(li) -> str:
    """목록 카드에서 파일데이터명 텍스트만 최대한 추출한다."""
    title_candidates: list[str] = []

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

    if not title_candidates:
        title_candidates.append(strip_list_badges(li.get_text(" ")))

    # 기관 prefix 판별에는 '_'가 있는 제목을 우선한다.
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
    if not n_prefix:
        return False

    # 정확 일치가 기본. 접두/포함은 아주 짧은 오탐을 막기 위해 3자 이상에서만 허용한다.
    if n_prefix == n_input:
        return True
    if len(n_input) >= 3 and (n_prefix.startswith(n_input) or n_input.startswith(n_prefix)):
        return True
    return False


def extract_value_from_text_by_label(text: str, label: str) -> str:
    text = clean_text(text)
    if not text:
        return ""

    others = [re.escape(x) for x in STOP_LABELS if x != label]
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

    중요:
    - keyword 검색 결과에는 설명/키워드에 입력값이 포함된 타기관 데이터가 섞인다.
    - 후보 채택은 반드시 목록명 prefix 기준으로 한다.
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

        text = clean_text(li.get_text(" "))
        org = extract_value_from_text_by_label(text, "제공기관")
        _add_candidate(org_names, org)

        prefix = org_prefix_from_dataset_title(title)
        _add_candidate(org_names, prefix)

    # li 구조가 안 잡히는 특수 HTML일 때만 링크 제목 prefix fallback.
    if not org_names:
        for a in soup.select("a[href*='/data/'], a[href*='/dataset/']"):
            title = strip_list_badges(a.get_text(" "))
            if n_input and not title_prefix_matches_input(title, user_input):
                continue
            prefix = org_prefix_from_dataset_title(title)
            _add_candidate(org_names, prefix)

    return list(dict.fromkeys(org_names))


def file_count_from_html(html: str) -> int | None:
    text = clean_text(BeautifulSoup(html or "", "lxml").get_text(" "))
    m = re.search(r"파일데이터\s*\(\s*([0-9,]+)\s*건\s*\)", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except Exception:
        return None


def has_dataset_items(html: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
    count = file_count_from_html(html)
    if count == 0:
        return False
    if count and count > 0:
        return True
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


def has_prefix_matched_items(html: str, user_input: str) -> bool:
    soup = BeautifulSoup(html or "", "lxml")
    selectors = [
        "div.result-list ul li",
        "#fileDataList ul li",
        "ul.result-list li",
        "ul.data-list li",
    ]
    for li in soup.select(", ".join(selectors)):
        title = get_dataset_title_from_card(li)
        if title_prefix_matches_input(title, user_input):
            return True
    for a in soup.select("a[href*='/data/'], a[href*='/dataset/']"):
        title = strip_list_badges(a.get_text(" "))
        if title_prefix_matches_input(title, user_input):
            return True
    return False


def _fetch_probe(url: str, headers: dict | None, timeout: int, user_input: str = "") -> dict[str, Any]:
    try:
        res = requests.get(url, headers=headers or DEFAULT_HEADERS, timeout=timeout)
        res.raise_for_status()
        html = res.text
        org_names = extract_org_names_from_list_html(html, user_input=user_input)
        file_count = file_count_from_html(html)
        prefix_match = has_prefix_matched_items(html, user_input) if user_input else False
        return {
            "ok": True,
            "url": url,
            "html": html,
            "org_names": org_names,
            "has_items": has_dataset_items(html),
            "has_prefix_items": prefix_match,
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
            "has_prefix_items": False,
            "file_count": None,
            "error": repr(e),
        }


def url_has_dataset_items(url: str, *, headers: dict | None = None, timeout: int = 5) -> bool:
    if not clean_text(url):
        return False
    r = _fetch_probe(url, headers=headers, timeout=timeout)
    return bool(r.get("has_items"))


def url_has_prefix_matched_items(url: str, user_input: str, *, headers: dict | None = None, timeout: int = 5) -> bool:
    if not clean_text(url) or not clean_text(user_input):
        return False
    r = _fetch_probe(url, headers=headers, timeout=timeout, user_input=user_input)
    return bool(r.get("has_prefix_items"))


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
    elif len(n_input) >= 3 and (n_org.startswith(n_input) or n_input.startswith(n_org)):
        score = 80

    score += min(count, 20)
    score -= max(0, len(n_org) - len(n_input)) // 4
    return score, org


def resolve_org_name_and_url_fast(
    user_input: str,
    *,
    headers: dict | None = None,
    timeout: int = 3,
    per_page: int = 10,
    max_workers: int = 4,
) -> dict[str, Any]:
    """
    사용자 입력값으로 실제 제공기관명과 대표 URL을 빠르게 확정한다.

    found=False인 경우 url은 keyword/orgSearch fallback일 수 있으므로,
    runner는 이 URL을 그대로 수집하면 안 되고 prefix_filter와 함께 써야 한다.
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
            "needs_prefix_filter": False,
            "probe_results": [],
        }

    probe_urls = build_org_search_probe_urls(raw, current_page=1, per_page=per_page)
    results: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(probe_urls)))) as ex:
        futures = [ex.submit(_fetch_probe, url, headers, timeout, raw) for url in probe_urls]
        for fut in as_completed(futures):
            results.append(fut.result())

    org_counter: Counter[str] = Counter()
    first_source_by_org: dict[str, str] = {}
    first_success_url = ""
    first_prefix_url = ""
    first_items_url = ""
    first_items_count = 0

    for r in results:
        if r.get("ok") and not first_success_url:
            first_success_url = r.get("url", "")
        if r.get("has_prefix_items") and not first_prefix_url:
            first_prefix_url = r.get("url", "")
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
            "has_prefix_items": r.get("has_prefix_items"),
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
            "source_url": first_source_by_org.get(exact_org, first_prefix_url or first_items_url or first_success_url),
            "item_count": int(max(first_items_count, sum(org_counter.values()))),
            "needs_prefix_filter": False,
            "prefix_filter": exact_org,
            "probe_results": probe_summary,
        }

    # 후보는 못 찾았지만 prefix에 맞는 목록은 있으면 keyword/orgSearch URL을 fallback으로 쓸 수 있다.
    # 단, 수집 runner에서 반드시 prefix_filter를 적용해야 한다.
    fallback_url = first_prefix_url or first_items_url or build_keyword_url(raw, current_page=1, per_page=per_page)
    return {
        "found": False,
        "exact_org": raw,
        "candidates": [],
        "url": fallback_url,
        "source_url": first_prefix_url or first_items_url or first_success_url,
        "item_count": int(first_items_count or 0),
        "needs_prefix_filter": bool(first_prefix_url),
        "prefix_filter": raw,
        "probe_results": probe_summary,
    }


def build_collection_target(
    org_input: str,
    target_url: str = "",
    *,
    headers: dict | None = None,
    timeout: int = 5,
    per_page: int = 1000,
    allow_keyword_fallback: bool = True,
) -> dict[str, Any]:
    """
    실제 수집 runner가 사용할 target URL을 확정한다.

    반환값:
    - exact_org: 저장 파일/표시에 사용할 기관명
    - target_url: 수집 URL
    - target_urls: 후보 URL 목록
    - title_prefix_filter: keyword/orgSearch fallback 시 반드시 적용할 목록명 prefix 필터
    - mode: exact_org_filter | direct_input_url | direct_raw_org | keyword_prefix_fallback | unresolved
    """
    raw = clean_text(org_input)
    given_url = clean_text(target_url)
    debug: dict[str, Any] = {
        "input_org": raw,
        "input_target_url": given_url,
        "steps": [],
    }

    # 1) UI/사용자 URL이 정확한 기관 필터 URL이고 실제 목록이 있으면 최우선 사용.
    if given_url and is_exact_org_filter_url(given_url):
        verified = url_has_dataset_items(update_list_url(given_url, current_page=1, per_page=10), headers=headers, timeout=timeout)
        debug["steps"].append({"step": "check_input_url", "url": given_url, "verified": verified})
        if verified:
            exact = get_org_from_filter_url(given_url) or raw
            return {
                "found": True,
                "exact_org": exact,
                "target_url": update_list_url(given_url, current_page=1, per_page=per_page),
                "target_urls": [update_list_url(given_url, current_page=1, per_page=per_page)],
                "title_prefix_filter": "",
                "mode": "direct_input_url",
                "debug": debug,
            }

    # 2) 포털 목록 기준 실제 제공기관명 해석.
    resolved = resolve_org_name_and_url_fast(raw, headers=headers, timeout=timeout, per_page=10, max_workers=4)
    debug["resolved"] = resolved

    target_urls: list[str] = []
    exact_org = clean_text(resolved.get("exact_org")) or raw

    # 2-1) 후보 기관명들의 정확 필터 URL을 먼저 검증.
    candidate_orgs = list(dict.fromkeys((resolved.get("candidates") or []) + ([exact_org] if exact_org else [])))
    for org in candidate_orgs:
        org_url = build_org_filter_url(org, current_page=1, per_page=per_page)
        check_url = build_org_filter_url(org, current_page=1, per_page=10)
        ok = url_has_dataset_items(check_url, headers=headers, timeout=timeout)
        debug["steps"].append({"step": "check_exact_org", "org": org, "url": check_url, "verified": ok})
        if ok:
            target_urls.append(org_url)
            return {
                "found": True,
                "exact_org": org,
                "target_url": org_url,
                "target_urls": list(dict.fromkeys(target_urls)),
                "title_prefix_filter": "",
                "mode": "exact_org_filter",
                "debug": debug,
            }

    # 3) 사용자가 이미 정확 기관명을 넣은 특수 케이스 대응: orgFilter/raw, org 단독 각각 확인.
    for raw_url in [
        build_org_filter_url(raw, current_page=1, per_page=per_page),
        build_simple_org_url(raw, current_page=1, per_page=per_page),
    ]:
        check_url = update_list_url(raw_url, current_page=1, per_page=10)
        ok = url_has_dataset_items(check_url, headers=headers, timeout=timeout)
        debug["steps"].append({"step": "check_raw_org", "url": check_url, "verified": ok})
        if ok:
            return {
                "found": True,
                "exact_org": raw,
                "target_url": raw_url,
                "target_urls": [raw_url],
                "title_prefix_filter": "",
                "mode": "direct_raw_org",
                "debug": debug,
            }

    # 4) 정확 기관 필터가 0건이지만 keyword/orgSearch에서 목록명 prefix가 맞는 항목이 있으면 prefix-filter 수집으로 fallback.
    #    이 모드는 타기관 혼입 방지를 위해 수집 엔진에서 반드시 title_prefix_filter를 적용해야 한다.
    if allow_keyword_fallback:
        fallback_candidates = []
        if clean_text(resolved.get("source_url")):
            fallback_candidates.append(update_list_url(resolved["source_url"], current_page=1, per_page=per_page))
        fallback_candidates.extend([
            build_keyword_url(raw, current_page=1, per_page=per_page),
            build_org_search_url(raw, current_page=1, per_page=per_page),
        ])
        for fb_url in list(dict.fromkeys(fallback_candidates)):
            check_url = update_list_url(fb_url, current_page=1, per_page=10)
            ok = url_has_prefix_matched_items(check_url, raw, headers=headers, timeout=timeout)
            debug["steps"].append({"step": "check_keyword_prefix", "url": check_url, "verified": ok})
            if ok:
                return {
                    "found": True,
                    "exact_org": exact_org or raw,
                    "target_url": fb_url,
                    "target_urls": [fb_url],
                    "title_prefix_filter": raw,
                    "mode": "keyword_prefix_fallback",
                    "debug": debug,
                }

    return {
        "found": False,
        "exact_org": exact_org or raw,
        "target_url": "",
        "target_urls": [],
        "title_prefix_filter": "",
        "mode": "unresolved",
        "debug": debug,
    }


def pick_url_candidates_for_collection(org_name: str, *, resolved_url: str = "", per_page: int = 1000) -> list[str]:
    """하위 호환용. 정확 URL 후보만 반환한다. keyword URL은 혼입 위험 때문에 여기서 반환하지 않는다."""
    org = clean_text(org_name)
    urls: list[str] = []
    if resolved_url:
        urls.append(update_list_url(resolved_url, current_page=1, per_page=per_page))
    if org:
        urls.append(build_org_filter_url(org, current_page=1, per_page=per_page))
        urls.append(build_simple_org_url(org, current_page=1, per_page=per_page))
    return list(dict.fromkeys(urls))
