# -*- coding: utf-8 -*-
"""공공데이터포털 크롤러 공통 유틸.

v5 핵심 원칙
- 기관명 변형을 하드코딩해 맞히지 않는다.
- 사용자가 입력한 키워드로 포털 파일데이터 검색을 먼저 수행한다.
- 검색 결과에 실제로 표시된 '제공기관' 후보를 추출한다.
- 후보가 여러 개면 Streamlit UI에서 사용자가 직접 선택한다.
- 선택된 제공기관명으로 org / orgFilter / orgFullName URL을 생성한다.
"""
from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.data.go.kr"
LIST_URL = f"{BASE_URL}/tcs/dss/selectDataSetList.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}

# 목록 카드에서 제공기관 값 뒤에 붙을 수 있는 다음 라벨들.
ORG_STOP_LABELS = [
    "분류체계", "수정일", "등록일", "조회수", "조회 수", "다운로드수", "다운로드 수", "다운로드",
    "키워드", "관리부서", "제공형태", "확장자", "파일데이터", "오픈API", "표준데이터셋",
    "데이터셋", "목록", "미리보기",
]

COMPANY_WORD_PATTERNS = [
    r"주식회사", r"\(주\)", r"㈜", r"（주）", r"\[주\]", r"유한회사", r"\(유\)", r"㈲",
    r"재단법인", r"\(재\)", r"사단법인", r"\(사\)",
]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def clean_filename(value: str, fallback: str = "unnamed") -> str:
    text = clean_text(value)
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def compact_for_match(value: str) -> str:
    """제공기관 후보 정렬용 일반 정규화.

    회사 표기만 제거하고 지역/기관명 자체는 임의 치환하지 않는다.
    예: 강원도 ↔ 강원특별자치도 같은 행정구역 별칭도 자동 치환하지 않는다.
    이런 케이스는 후보를 보여주고 사용자가 선택한다.
    """
    s = clean_text(value)
    s = s.replace("（", "(").replace("）", ")")
    s = s.replace("·", "").replace("ㆍ", "")
    for pat in COMPANY_WORD_PATTERNS:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = re.sub(r"[\s\-_./,|:：\[\]{}<>]+", "", s)
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return s.lower()


def token_list(value: str) -> list[str]:
    return re.findall(r"[가-힣A-Za-z0-9]{2,}", clean_text(value))


def token_set(value: str) -> set[str]:
    return set(token_list(value))


def candidate_score(query: str, provider: str) -> int:
    """후보 정렬용 점수. 자동확정 용도가 아니라 보기 좋게 정렬하기 위한 값.

    특정 행정구역 별칭을 하드코딩하지 않는다. 다만 사용자가 여러 단어로
    검색했는데 후보가 마지막 단어 하나만 가진 경우(예: '강원도 고성군' vs '고성군')는
    덜 구체적인 후보로 보고 정렬점수를 낮춘다.
    """
    q = compact_for_match(query)
    p = compact_for_match(provider)
    if not q or not p:
        return 0

    q_tokens = token_list(query)
    p_tokens = token_list(provider)
    q_set = set(q_tokens)
    p_set = set(p_tokens)

    score = 0
    if q == p:
        score = 100
    elif q in p:
        score = max(70, 95 - abs(len(p) - len(q)) * 3)
    elif p in q:
        score = max(35, 75 - abs(len(p) - len(q)) * 3)
    elif q_set and p_set:
        overlap = len(q_set & p_set) / max(1, len(q_set | p_set))
        if overlap:
            score = int(overlap * 60)

    # 여러 단어 질의에서 후보가 마지막 단어 하나만 있으면 덜 구체적인 후보로 감점
    if len(q_tokens) >= 2 and len(p_tokens) <= 1:
        score -= 25

    # 여러 단어 질의에서 후보도 여러 단어이고 마지막 핵심어가 포함되면 구체 후보로 가점
    if len(q_tokens) >= 2 and len(p_tokens) >= 2 and q_tokens[-1] in p_set:
        score += 20

    return max(0, min(100, score))


def build_file_list_url(
    org_name: str | None = None,
    *,
    current_page: int = 1,
    per_page: int = 10,
    keyword: str = "",
    sort: str = "updtDt",
    d_type: str = "",
) -> str:
    """공공데이터포털 파일데이터 목록 URL 생성.

    org_name이 있으면 선택된 제공기관명 그대로 org/orgFilter/orgFullName에 넣는다.
    org_name이 없으면 전체 파일데이터 목록 URL을 만든다.
    """
    org = clean_text(org_name)
    params = {
        "conditionType": "search",
        "dType": clean_text(d_type),
        "keyword": clean_text(keyword),
        "org": org,
        "orgFilter": org,
        "orgFullName": org,
        "currentPage": str(int(current_page or 1)),
        "perPage": str(int(per_page or 10)),
        "sort": sort,
    }
    if not org:
        params.update({
            "detailKeyword": "",
            "publicDataPk": "",
            "recmSe": "N",
            "detailText": "",
            "relatedKeyword": "",
            "commaNotInData": "",
            "commaAndData": "",
            "commaOrData": "",
            "must_not": "",
            "tabId": "",
            "dataSetCoreTf": "",
            "coreDataNm": "",
            "relRadio": "",
            "brm": "",
            "instt": "",
            "svcType": "",
            "kwrdArray": "",
            "extsn": "",
            "coreDataNmArray": "",
            "operator": "AND",
            "pblonsipScopeCode": "PBDE07",
        })
    return LIST_URL + "?" + urllib.parse.urlencode(params)


def build_keyword_search_url(
    keyword: str,
    *,
    current_page: int = 1,
    per_page: int = 100,
    sort: str = "updtDt",
    d_type: str = "FILE",
) -> str:
    """제공기관 후보 탐색용 키워드 검색 URL."""
    params = {
        "conditionType": "search",
        "dType": clean_text(d_type),
        "keyword": clean_text(keyword),
        "currentPage": str(int(current_page or 1)),
        "perPage": str(int(per_page or 100)),
        "sort": sort,
        "org": "",
        "orgFilter": "",
        "orgFullName": "",
    }
    return LIST_URL + "?" + urllib.parse.urlencode(params)


def extract_provider_from_card_text(text: str) -> str:
    """목록 카드 텍스트에서 '제공기관' 값을 추출."""
    text = clean_text(text)
    if not text or "제공기관" not in text:
        return ""
    stop = "|".join(re.escape(x) for x in sorted(ORG_STOP_LABELS, key=len, reverse=True))
    patterns = [
        rf"제공\s*기관\s*[:：]?\s*(.*?)(?=\s*(?:{stop})\s*[:：]?\s*|$)",
        rf"제공기관\s*[:：]?\s*(.*?)(?=\s*(?:{stop})\s*[:：]?\s*|$)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            value = clean_text(m.group(1))
            value = re.sub(r"^[:：\-\s]+", "", value)
            value = clean_text(value)
            if 1 < len(value) <= 100:
                return value
    return ""


def _title_from_card(li) -> str:
    title_el = li.select_one("span.title, .title")
    if title_el:
        title = clean_text(title_el.get("title") or title_el.get_text(" "))
    else:
        a = li.select_one("a[href*='/data/'], a[href*='/dataset/']")
        title = clean_text((a.get("title") if a else "") or (a.get_text(" ") if a else ""))
    title = re.sub(r"^(CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP)\s*(\+|,|/|-)?\s*", "", title, flags=re.I)
    return clean_text(title.replace("미리보기", ""))[:120]


def extract_org_candidates_from_search_html(html: str, query: str = "") -> list[dict[str, Any]]:
    """포털 검색 결과 HTML에서 실제 제공기관 후보를 추출한다.

    반환 예시:
    [{"provider": "강원특별자치도 고성군", "count": 3, "score": 82, "samples": "..."}]
    """
    soup = BeautifulSoup(html or "", "lxml")
    counts: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)

    def add(provider: str, *, sample: str = "", source: str = ""):
        provider = clean_text(provider)
        provider = re.sub(r"\s+(분류체계|수정일|등록일|조회수|다운로드).*$", "", provider).strip()
        if not provider or len(provider) < 2 or len(provider) > 100:
            return
        counts[provider] += 1
        if sample and len(samples[provider]) < 3:
            samples[provider].append(sample)
        if source:
            sources[provider].add(source)

    # 1) 목록 카드의 제공기관 라벨에서 추출
    for li in soup.select("div.result-list ul li, #fileDataList ul li"):
        txt = clean_text(li.get_text(" "))
        provider = extract_provider_from_card_text(txt)
        title = _title_from_card(li)
        add(provider, sample=title, source="목록카드")

    # 2) 혹시 기관 링크 query에 제공기관명이 있는 경우 추출
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        parsed = urllib.parse.urlparse(urllib.parse.urljoin(BASE_URL, href))
        qs = urllib.parse.parse_qs(parsed.query)
        for key in ["orgFullName", "orgFilter", "org", "instt"]:
            for value in qs.get(key, []):
                add(value, sample=clean_text(a.get_text(" "))[:120], source=f"link:{key}")

    rows: list[dict[str, Any]] = []
    for provider, count in counts.items():
        rows.append({
            "provider": provider,
            "count": int(count),
            "score": int(candidate_score(query, provider)),
            "samples": " / ".join([x for x in samples.get(provider, []) if x]),
            "sources": ", ".join(sorted(sources.get(provider, []))),
            "url": build_file_list_url(provider, current_page=1, per_page=1000, d_type=""),
        })
    # 여러 단어 검색어는 더 구체적인 제공기관명(토큰 수가 많은 후보)을 위에 배치한다.
    # 이는 자동 선택용이 아니라 사용자가 고를 후보의 표시 순서를 개선하기 위한 일반 규칙이다.
    q_specific = len(token_list(query)) >= 2
    rows.sort(key=lambda r: (
        -len(token_list(r["provider"])) if q_specific else 0,
        -int(r["score"]),
        -int(r["count"]),
        r["provider"],
    ))
    return rows


def discover_org_candidates_by_keyword(
    keyword: str,
    *,
    max_pages: int = 2,
    per_page: int = 100,
    timeout: int = 12,
) -> list[dict[str, Any]]:
    """requests 기반 제공기관 후보 탐색."""
    query = clean_text(keyword)
    if not query:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for page_no in range(1, max(1, int(max_pages or 1)) + 1):
        url = build_keyword_search_url(query, current_page=page_no, per_page=per_page, d_type="FILE")
        try:
            res = requests.get(url, headers=HEADERS, timeout=timeout)
            res.raise_for_status()
        except Exception:
            continue
        for row in extract_org_candidates_from_search_html(res.text, query=query):
            p = row["provider"]
            if p not in merged:
                merged[p] = row
            else:
                merged[p]["count"] += row.get("count", 0)
                if row.get("samples"):
                    merged[p]["samples"] = clean_text(merged[p].get("samples", "") + " / " + row["samples"]).strip(" /")
                merged[p]["score"] = max(int(merged[p].get("score", 0)), int(row.get("score", 0)))
    rows = list(merged.values())
    q_specific = len(token_list(query)) >= 2
    rows.sort(key=lambda r: (
        -len(token_list(str(r.get("provider", "")))) if q_specific else 0,
        -int(r.get("score", 0)),
        -int(r.get("count", 0)),
        r.get("provider", ""),
    ))
    return rows


def candidate_rows_to_names(rows: list[dict[str, Any]]) -> list[str]:
    return [clean_text(r.get("provider")) for r in rows if clean_text(r.get("provider"))]


def build_url_for_selected_org(provider_name: str, *, per_page: int = 1000, d_type: str = "") -> str:
    return build_file_list_url(provider_name, current_page=1, per_page=per_page, d_type=d_type)


def describe_org_resolution_strategy(user_input: str = "") -> str:
    q = clean_text(user_input)
    if not q:
        return "기관명을 입력한 뒤 후보 조회를 누르면 포털 검색 결과에 실제로 표시된 제공기관 후보를 가져옵니다."
    return (
        f"'{q}' 키워드로 포털 파일데이터를 검색하고, 검색 결과 카드에 표시된 제공기관 후보를 추출합니다. "
        "후보가 여러 개면 자동확정하지 않고 사용자가 직접 선택합니다."
    )


def build_chromium_launch_kwargs(headless: bool = True, browser_executable_path: str | None = None) -> dict:
    """로컬/Streamlit Cloud/GitHub 배포 환경에서 Chromium 실행 경로를 보정."""
    kwargs = {"headless": bool(headless)}
    candidates: list[str] = []
    if browser_executable_path:
        candidates.append(browser_executable_path)
    candidates.extend([
        os.environ.get("CHROMIUM_EXECUTABLE_PATH", ""),
        os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
    ])
    for path in candidates:
        if path and Path(path).exists():
            kwargs["executable_path"] = path
            return kwargs
    return kwargs


def file_size_label(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    size = p.stat().st_size
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
