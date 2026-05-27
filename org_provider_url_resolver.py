# -*- coding: utf-8 -*-
"""
org_provider_url_resolver.py

목표
- 기관별 크롤러들이 사용할 '기관의 파일데이터 목록 URL'만 빠르게 확보한다.
- 상세 URL 전체를 먼저 수집하지 않는다.
- org=기관명 직접 조립 방식은 사용하지 않는다.
- 검색 결과/상세페이지에서 포털이 실제로 제공하는 제공기관 링크를 찾아 파일데이터 탭 URL로 변환한다.

동작
1) 기관명 일부 키워드로 파일데이터 목록 검색
2) 목록 카드에서 제공기관 후보를 우선 추출
3) 부족할 때만 일부 상세페이지를 열어 제공기관명/제공기관 링크 추출
4) 사용자가 기관 선택
5) 선택 기관과 일치하는 상세페이지 1개를 찾아 제공기관 링크 추출
6) 해당 링크를 dType=FILE 목록 URL로 정규화
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

BASE_URL = "https://www.data.go.kr"
LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)

# 사용자 화면에는 노출하지 않는 내부값
CANDIDATE_PAGES = 3
CANDIDATE_PER_PAGE = 50
DETAIL_FALLBACK_LIMIT = 15
URL_RESOLVE_PAGES = 4
URL_RESOLVE_PER_PAGE = 50
SCORE_THRESHOLD = 80

@dataclass
class ListItem:
    title: str
    detail_url: str
    provider_name: str = ""
    view_count: str = ""
    download_count: str = ""
    source_list_url: str = ""

@dataclass
class ProviderCandidate:
    provider_name: str
    hit_count: int = 1
    score: int = 0

@dataclass
class ProviderUrlResult:
    provider_name: str
    provider_filedata_url: str
    provider_original_url: str = ""
    matched_detail_url: str = ""
    matched_title: str = ""
    matched_score: int = 0
    total_list_items_checked: int = 0
    method: str = "provider_link_from_detail_page"


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_org(value: str) -> str:
    s = clean_text(value).lower()
    for token in ["㈜", "(주)", "（주）", "주식회사", " ", "·", "ㆍ", "-", "_", ".", ",", "(", ")"]:
        if token in ["㈜", "(주)", "（주）", "주식회사"]:
            s = s.replace(token, "주")
        else:
            s = s.replace(token, "")
    return s


def strip_company_marker(value: str) -> str:
    s = clean_text(value)
    s = s.replace("㈜", "")
    s = s.replace("(주)", "")
    s = s.replace("（주）", "")
    s = s.replace("주식회사", "")
    return clean_text(s)


def partial_ratio(shorter: str, longer: str) -> float:
    shorter = clean_text(shorter)
    longer = clean_text(longer)
    if not shorter or not longer:
        return 0.0
    if len(shorter) > len(longer):
        shorter, longer = longer, shorter
    if shorter in longer:
        return 1.0
    n = len(shorter)
    best = 0.0
    for i in range(0, max(1, len(longer) - n + 1)):
        best = max(best, SequenceMatcher(None, shorter, longer[i:i+n]).ratio())
    return best


def provider_similarity_score(query: str, provider: str) -> int:
    q = normalize_org(query)
    p = normalize_org(provider)
    if not q or not p:
        return 0
    if q == p or q in p or p in q:
        return 100
    variants_q = [q]
    variants_p = [p]
    if q.startswith("한국") and len(q) > 2:
        variants_q.append(q[2:])
    if p.startswith("한국") and len(p) > 2:
        variants_p.append(p[2:])
    best = 0.0
    for a in variants_q:
        for b in variants_p:
            best = max(best, SequenceMatcher(None, a, b).ratio(), partial_ratio(a, b))
    return int(round(best * 100))


def is_detail_url(url: str) -> bool:
    u = clean_text(url).lower()
    return bool(
        u and ("/data/" in u or "/dataset/" in u) and
        ("filedata.do" in u or re.search(r"/(?:data|dataset)/\d+", u))
    )


def absolute_url(href: str, base: str = BASE_URL) -> str:
    return urllib.parse.urljoin(base, href or "")


def build_headers(referer: str = BASE_URL + "/") -> Dict[str, str]:
    return {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "close",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Referer": referer,
    }

_SESSION: Optional[requests.Session] = None


def get_session() -> requests.Session:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _SESSION = session
    return _SESSION


def get_html(url: str, referer: str = BASE_URL + "/", timeout: int = 25, attempts: int = 5) -> str:
    global _SESSION
    last_err = None
    session = get_session()
    for attempt in range(1, attempts + 1):
        try:
            if attempt == 1:
                time.sleep(random.uniform(0.15, 0.45))
            else:
                time.sleep(min(12.0, 0.8 * (2 ** (attempt - 2))) + random.uniform(0.4, 1.2))
            resp = session.get(url, headers=build_headers(referer), timeout=(8, timeout), verify=True)
            if resp.status_code in {403, 429, 500, 502, 503, 504}:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {url}")
                try:
                    resp.close()
                except Exception:
                    pass
                continue
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = "utf-8"
            text = resp.text or ""
            try:
                resp.close()
            except Exception:
                pass
            return text
        except (requests.ConnectionError, requests.ReadTimeout, requests.ChunkedEncodingError, OSError) as e:
            last_err = e
            if "reset" in repr(e).lower() or "Connection aborted" in repr(e):
                try:
                    session.close()
                except Exception:
                    pass
                _SESSION = None
                session = get_session()
            continue
    raise RuntimeError(f"HTML 요청 실패: {repr(last_err)} | URL={url}")


def build_keyword_file_list_url(keyword: str, page: int = 1, per_page: int = 50) -> str:
    params = {
        "dType": "FILE",
        "keyword": keyword,
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
        "sort": "updtDt",
        "relRadio": "",
        "orgFullName": "",
        "orgFilter": "",
        "org": "",
        "orgSearch": "",
        "currentPage": str(page),
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
    return LIST_URL + "?" + urllib.parse.urlencode(params, doseq=True)


def with_page(url: str, page: int = 1, per_page: int = 50) -> str:
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    q["dType"] = "FILE"
    q["currentPage"] = str(page)
    q["perPage"] = str(per_page)
    q.setdefault("sort", "updtDt")
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))


def force_filedata_list_url(url: str) -> str:
    """상세페이지 제공기관 링크를 기관의 파일데이터 목록 URL로 정규화한다."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(absolute_url(url))
    q = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    q["dType"] = "FILE"
    q["currentPage"] = "1"
    q.setdefault("perPage", "10")
    q.setdefault("sort", "updtDt")
    # 검색어가 섞여 있으면 기관 링크 성격이 흐려질 수 있어 비운다.
    for k in ["keyword", "detailKeyword", "detailText", "relatedKeyword", "commaNotInData", "commaAndData", "commaOrData", "must_not"]:
        q[k] = ""
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))


def clean_title(raw: str) -> str:
    s = clean_text(raw)
    s = re.sub(
        r"^((CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|API|오픈API)\s*(\+|,|/|-)?\s*)+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    for marker in [" 제공기관 ", " 분류체계 ", " 수정일 ", " 등록일 ", " 조회수 ", " 다운로드 "]:
        pos = s.find(marker)
        if pos > 0:
            s = s[:pos]
    return clean_text(s).replace("미리보기", "").strip()


def extract_count(text: str, labels: List[str]) -> str:
    text = clean_text(text)
    for label in labels:
        label_re = re.escape(label).replace(r"\ ", r"\s*")
        m = re.search(rf"{label_re}\s*[:：]?\s*([0-9][0-9,]*)", text)
        if m:
            return m.group(1).replace(",", "")
    return ""


def extract_labeled_value(text: str, label: str) -> str:
    text = clean_text(text)
    labels = ["제공기관", "분류체계", "등록일", "수정일", "조회수", "조회 수", "다운로드수", "다운로드 수", "다운로드", "키워드", "관리부서"]
    stop = "|".join(re.escape(x) for x in labels if x != label)
    m = re.search(rf"{re.escape(label)}\s*[:：]?\s*(.*?)(?=\s+(?:{stop})\s*[:：]?|$)", text)
    if not m:
        return ""
    value = clean_text(m.group(1)).strip(" ,;|/")
    if len(value) > 80:
        return ""
    return value


def parse_list_items(html: str, page_url: str) -> List[ListItem]:
    soup = BeautifulSoup(html or "", "lxml")
    nodes = soup.select("div.result-list ul li, #fileDataList ul li")
    if not nodes:
        nodes = soup.select("a[href*='/data/'], a[href*='/dataset/']")
    out: List[ListItem] = []
    seen = set()
    for node in nodes:
        if getattr(node, "name", "") == "a":
            a = node
            text = clean_text(a.get_text(" "))
        else:
            a = None
            for cand in node.select("a[href*='/data/'], a[href*='/dataset/']"):
                full = absolute_url(cand.get("href", ""), page_url)
                if is_detail_url(full):
                    a = cand
                    break
            text = clean_text(node.get_text(" "))
        if not a:
            continue
        url = absolute_url(a.get("href", ""), page_url)
        if not is_detail_url(url) or url in seen:
            continue
        seen.add(url)
        title_candidates = [clean_text(a.get(attr)) for attr in ["title", "aria-label", "data-title"] if clean_text(a.get(attr))]
        title_candidates.append(clean_text(a.get_text(" ")))
        title = clean_title(max(title_candidates, key=len) if title_candidates else text)
        provider = extract_labeled_value(text, "제공기관")
        view = extract_count(text, ["조회수", "조회 수"])
        down = extract_count(text, ["다운로드수", "다운로드 수", "다운로드"])
        out.append(ListItem(title=title, detail_url=url, provider_name=provider, view_count=view, download_count=down, source_list_url=page_url))
    return out


def collect_list_items_from_url(list_url: str, max_pages: int, per_page: int) -> List[ListItem]:
    all_items: List[ListItem] = []
    seen = set()
    referer = BASE_URL + "/"
    for page in range(1, max(1, int(max_pages)) + 1):
        url = with_page(list_url, page, per_page)
        html = get_html(url, referer=referer)
        referer = url
        items = parse_list_items(html, url)
        if not items:
            break
        new = 0
        for item in items:
            if item.detail_url not in seen:
                seen.add(item.detail_url)
                all_items.append(item)
                new += 1
        if new == 0:
            break
        time.sleep(random.uniform(0.4, 1.0))
    return all_items


def collect_list_items_by_keyword(keyword: str, max_pages: int = CANDIDATE_PAGES, per_page: int = CANDIDATE_PER_PAGE) -> List[ListItem]:
    return collect_list_items_from_url(build_keyword_file_list_url(keyword, 1, per_page), max_pages=max_pages, per_page=per_page)


def make_detail_url_candidates(detail_url: str) -> List[str]:
    urls = [detail_url]
    m = re.search(r"/(?:data|dataset)/(\d+)/fileData\.do", detail_url)
    if m:
        data_id = m.group(1)
        for u in [f"https://www.data.go.kr/data/{data_id}/fileData.do", f"https://www.data.go.kr/dataset/{data_id}/fileData.do?lang=ko"]:
            if u not in urls:
                urls.append(u)
    return urls


def extract_provider_from_detail_html(html: str, detail_url: str) -> Dict[str, str]:
    soup = BeautifulSoup(html or "", "lxml")
    dataset_name = ""
    provider_name = ""
    provider_url = ""
    for table in soup.select("table"):
        if "제공기관" not in clean_text(table.get_text(" ")):
            continue
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                cells = tr.find_all(["th", "td"])
            i = 0
            while i < len(cells) - 1:
                key = re.sub(r"\s+", "", cells[i].get_text(" ")).replace(":", "").replace("*", "")
                val_cell = cells[i + 1]
                value = clean_text(val_cell.get_text(" "))
                if key in {"파일데이터명", "데이터명", "제목"} and value and not dataset_name:
                    dataset_name = clean_title(value)
                if key == "제공기관":
                    provider_name = value
                    a = val_cell.select_one("a[href]")
                    if a:
                        provider_url = absolute_url(a.get("href", ""), detail_url)
                    return {"provider_name": provider_name, "provider_url": provider_url, "dataset_name": dataset_name}
                i += 2
    return {"provider_name": provider_name, "provider_url": provider_url, "dataset_name": dataset_name}


def read_provider_from_detail(detail_url: str) -> Dict[str, str]:
    last_html = ""
    for url in make_detail_url_candidates(detail_url):
        try:
            html = get_html(url, referer=BASE_URL + "/")
            last_html = html
            info = extract_provider_from_detail_html(html, detail_url)
            if info.get("provider_name"):
                return info
        except Exception:
            continue
    if last_html:
        return extract_provider_from_detail_html(last_html, detail_url)
    return {"provider_name": "", "provider_url": "", "dataset_name": ""}


def find_provider_candidates(keyword: str) -> Dict:
    """화면 표시용: 기관명만 후보로 보여주기 위한 함수."""
    keyword = clean_text(keyword)
    if not keyword:
        raise ValueError("기관명 검색어를 입력하세요.")
    items = collect_list_items_by_keyword(keyword, max_pages=CANDIDATE_PAGES, per_page=CANDIDATE_PER_PAGE)
    candidates: Dict[str, ProviderCandidate] = {}

    def add(provider: str):
        provider = clean_text(provider)
        if not provider:
            return
        score = provider_similarity_score(keyword, provider)
        if score < SCORE_THRESHOLD:
            return
        key = normalize_org(provider)
        if key not in candidates:
            candidates[key] = ProviderCandidate(provider_name=provider, hit_count=1, score=score)
        else:
            candidates[key].hit_count += 1
            candidates[key].score = max(candidates[key].score, score)

    no_provider_items: List[ListItem] = []
    for item in items:
        if item.provider_name:
            add(item.provider_name)
        else:
            no_provider_items.append(item)

    # 목록 카드에서 기관명이 안 잡히는 사이트 상태일 때만 상세 fallback 제한 확인
    fallback_items = no_provider_items if candidates else items
    for item in fallback_items[:DETAIL_FALLBACK_LIMIT]:
        try:
            info = read_provider_from_detail(item.detail_url)
            add(info.get("provider_name", ""))
        except Exception:
            pass

    candidate_list = [asdict(v) for v in candidates.values()]
    candidate_list.sort(key=lambda x: (-int(x.get("score", 0)), -int(x.get("hit_count", 0)), x.get("provider_name", "")))
    return {
        "keyword": keyword,
        "items_checked": len(items),
        "candidates": candidate_list,
    }


def provider_search_keywords(seed_keyword: str, provider_name: str) -> List[str]:
    values = [provider_name, strip_company_marker(provider_name), seed_keyword]
    base = strip_company_marker(provider_name)
    if base.startswith("한국") and len(base) > 2:
        values.append(base[2:])
    out = []
    for v in values:
        v = clean_text(v)
        if v and v not in out:
            out.append(v)
    return out


def resolve_provider_filedata_url(provider_name: str, seed_keyword: str) -> Dict:
    """선택 기관의 파일데이터 목록 URL 하나만 찾는다. 상세 URL 전체 수집 금지."""
    provider_name = clean_text(provider_name)
    seed_keyword = clean_text(seed_keyword)
    if not provider_name:
        raise ValueError("제공기관명이 없습니다.")

    checked = 0
    best_match: Optional[Tuple[int, ListItem, Dict[str, str]]] = None

    for kw in provider_search_keywords(seed_keyword, provider_name):
        items = collect_list_items_by_keyword(kw, max_pages=URL_RESOLVE_PAGES, per_page=URL_RESOLVE_PER_PAGE)
        for item in items:
            checked += 1
            # 목록 카드 제공기관이 명확히 다르면 상세페이지를 열지 않는다.
            if item.provider_name:
                card_score = provider_similarity_score(provider_name, item.provider_name)
                if card_score < SCORE_THRESHOLD:
                    continue
            try:
                info = read_provider_from_detail(item.detail_url)
                actual = info.get("provider_name", "")
                score = provider_similarity_score(provider_name, actual)
                if score >= SCORE_THRESHOLD and info.get("provider_url"):
                    result = ProviderUrlResult(
                        provider_name=actual or provider_name,
                        provider_filedata_url=force_filedata_list_url(info["provider_url"]),
                        provider_original_url=info["provider_url"],
                        matched_detail_url=item.detail_url,
                        matched_title=item.title or info.get("dataset_name", ""),
                        matched_score=score,
                        total_list_items_checked=checked,
                    )
                    return asdict(result)
                if score >= SCORE_THRESHOLD and best_match is None:
                    best_match = (score, item, info)
            except Exception:
                continue

    # provider_url 없는 경우는 기관 URL을 확정할 수 없으므로 실패 처리
    if best_match:
        score, item, info = best_match
        raise RuntimeError(
            f"제공기관명은 확인했지만 포털 제공기관 링크를 찾지 못했습니다. 기관={info.get('provider_name','')}, 상세URL={item.detail_url}"
        )
    raise RuntimeError("선택한 제공기관의 파일데이터 목록 URL을 찾지 못했습니다. 검색어를 기관명에 더 가깝게 입력해 주세요.")


def save_provider_url_result(result: Dict, output_dir: str = "outputs/resolution") -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    provider = clean_text(result.get("provider_name", "기관")) or "기관"
    safe = re.sub(r"[\\/:*?\"<>|]", "_", provider)
    path = Path(output_dir) / f"provider_url_{safe}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return str(path)
