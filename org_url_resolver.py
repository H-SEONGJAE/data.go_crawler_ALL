# -*- coding: utf-8 -*-
"""
org_url_resolver.py

기관명 일부 입력만으로 공공데이터포털 파일데이터 상세 URL을 확보하는 Resolver.

개선 방향
- org=기관명 URL 직접 조립 금지
- keyword 기반 파일데이터 검색 결과에서 상세 URL 수집
- 상세페이지의 제공기관명을 추출하고, 입력 검색어/선택 기관과의 유사도 점수 계산
- 80점 이상 후보만 화면에 표시
- ConnectionResetError 대응: Edge 계열 User-Agent + Session + Retry + 수동 재시도 + 지터 적용
"""

from __future__ import annotations

import json
import os
import re
import time
import random
import urllib.parse
import subprocess
import sys
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple
from difflib import SequenceMatcher

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.data.go.kr"
LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"

# 사용자가 제시한 Edge 계열 UA. 공공데이터포털이 단순 Python UA를 차단/리셋하는 경우를 줄이기 위한 기본값.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
)

DEFAULT_SCORE_THRESHOLD = 80


@dataclass
class ListItem:
    title: str
    detail_url: str
    view_count: str = ""
    download_count: str = ""
    source_list_url: str = ""
    source_keyword: str = ""


@dataclass
class ProviderCandidate:
    provider_name: str
    provider_url: str = ""
    sample_detail_url: str = ""
    sample_title: str = ""
    hit_count: int = 1
    max_score: int = 0
    avg_score: float = 0.0


@dataclass
class VerifiedItem:
    title: str
    detail_url: str
    provider_name: str
    provider_url: str = ""
    view_count: str = ""
    download_count: str = ""
    source_list_url: str = ""
    source_keyword: str = ""
    provider_score: int = 0
    validation_status: str = "SCORE_MATCHED"


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def norm_org(value: str) -> str:
    s = clean_text(value).lower()
    replacements = {
        "㈜": "주",
        "(주)": "주",
        "（주）": "주",
        "주식회사": "주",
        "공단": "공단",
        "공사": "공사",
    }
    for a, b in replacements.items():
        s = s.replace(a, b)
    s = re.sub(r"[\s\(\)\[\]\{\}·ㆍ\-_/\\,\.]+", "", s)
    return s


def strip_company_marker(value: str) -> str:
    s = clean_text(value)
    s = s.replace("㈜", "")
    s = s.replace("(주)", "")
    s = s.replace("주식회사", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _partial_ratio(shorter: str, longer: str) -> float:
    shorter = clean_text(shorter)
    longer = clean_text(longer)
    if not shorter or not longer:
        return 0.0
    if len(shorter) > len(longer):
        shorter, longer = longer, shorter
    if shorter in longer:
        return 1.0
    n = len(shorter)
    if n == 0:
        return 0.0
    best = 0.0
    # 한국어 기관명은 보통 짧으므로 전체 window 비교해도 부담이 작음
    for i in range(0, max(1, len(longer) - n + 1)):
        window = longer[i:i+n]
        best = max(best, SequenceMatcher(None, shorter, window).ratio())
    return best


def provider_similarity_score(query: str, provider: str) -> int:
    """기관명 유사도 점수. 포함 관계는 100점, 그 외 전체/부분 유사도 최대값."""
    q = norm_org(query)
    p = norm_org(provider)
    if not q or not p:
        return 0
    if q == p or q in p or p in q:
        return 100
    full = SequenceMatcher(None, q, p).ratio()
    partial = _partial_ratio(q, p)
    # '한국' prefix 때문에 점수가 낮아지는 경우 보정
    q2 = q[2:] if q.startswith("한국") and len(q) > 2 else q
    p2 = p[2:] if p.startswith("한국") and len(p) > 2 else p
    stripped = max(
        SequenceMatcher(None, q2, p2).ratio(),
        _partial_ratio(q2, p2),
    ) if q2 and p2 else 0
    return int(round(max(full, partial, stripped) * 100))


def org_matches(selected_provider: str, actual_provider: str, threshold: int = DEFAULT_SCORE_THRESHOLD) -> bool:
    return provider_similarity_score(selected_provider, actual_provider) >= int(threshold)


def is_detail_url(url: str) -> bool:
    u = clean_text(url).lower()
    return bool(
        u
        and ("/data/" in u or "/dataset/" in u)
        and ("filedata.do" in u or re.search(r"/(?:data|dataset)/\d+", u))
    )


def absolute_url(href: str, base: str = BASE_URL) -> str:
    return urllib.parse.urljoin(base, href or "")


def build_keyword_list_url(keyword: str, page: int = 1, per_page: int = 100) -> str:
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


def update_url_page_perpage(url: str, page: int, per_page: int) -> str:
    parsed = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    q["currentPage"] = str(page)
    q["perPage"] = str(per_page)
    q.setdefault("dType", "FILE")
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))


def extract_count_from_text(text: str, labels: List[str]) -> str:
    text = clean_text(text)
    for label in labels:
        label_re = re.escape(label).replace(r"\ ", r"\s*")
        m = re.search(rf"{label_re}\s*[:：]?\s*([0-9][0-9,]*)", text)
        if m:
            return m.group(1).replace(",", "")
    return ""


def extract_view_download(text: str) -> Tuple[str, str]:
    text = clean_text(text)
    pair_patterns = [
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드수?\s*[:：]?\s*([0-9][0-9,]*)",
    ]
    for pat in pair_patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).replace(",", ""), m.group(2).replace(",", "")
    return (
        extract_count_from_text(text, ["조회수", "조회 수"]),
        extract_count_from_text(text, ["다운로드수", "다운로드 수", "다운로드"]),
    )


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


def extract_title_from_anchor(a) -> str:
    if not a:
        return ""
    candidates = []
    for attr in ["title", "aria-label", "data-title", "data-name"]:
        v = clean_text(a.get(attr))
        if v:
            candidates.append(v)
    candidates.append(clean_text(a.get_text(" ")))
    cleaned = [clean_title(c) for c in candidates if clean_title(c)]
    if not cleaned:
        return ""
    return sorted(cleaned, key=lambda x: (("..." not in x and "…" not in x), len(x)), reverse=True)[0]


def parse_list_items_from_html(html: str, page_url: str, source_keyword: str = "") -> List[ListItem]:
    soup = BeautifulSoup(html or "", "lxml")
    results: List[ListItem] = []
    seen = set()
    containers = soup.select("div.result-list ul li, #fileDataList ul li")
    if not containers:
        containers = soup.select("a[href*='/data/'], a[href*='/dataset/']")

    for node in containers:
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
        href = absolute_url(a.get("href", ""), page_url)
        if not is_detail_url(href) or href in seen:
            continue
        seen.add(href)
        title = extract_title_from_anchor(a) or clean_title(text)
        view, download = extract_view_download(text)
        results.append(ListItem(title=title, detail_url=href, view_count=view, download_count=download, source_list_url=page_url, source_keyword=source_keyword))
    return results


# ==========================================================
# requests helpers: UA + Retry + manual retry
# ==========================================================
_SESSION: requests.Session | None = None


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
        "Sec-Fetch-Site": "same-origin" if referer.startswith(BASE_URL) else "none",
        "Sec-Fetch-User": "?1",
        "Referer": referer,
    }


def get_retry_session() -> requests.Session:
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    session = requests.Session()
    retry = Retry(
        total=4,
        connect=4,
        read=4,
        status=4,
        backoff_factor=0.9,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_html_requests(url: str, timeout: int = 25, max_attempts: int = 5, referer: str = BASE_URL + "/") -> str:
    """requests로 HTML을 읽되, 연결 리셋/일시 차단을 대비해 수동 재시도한다."""
    session = get_retry_session()
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                delay = min(18.0, (1.2 * (2 ** (attempt - 2))) + random.uniform(0.5, 1.8))
                time.sleep(delay)
            else:
                time.sleep(random.uniform(0.25, 0.75))
            resp = session.get(url, headers=build_headers(referer=referer), timeout=(8, timeout), verify=True)
            status = resp.status_code
            if status in {403, 429, 500, 502, 503, 504}:
                last_err = RuntimeError(f"HTTP {status}: {url}")
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
        except (requests.ConnectionError, requests.ReadTimeout, requests.ChunkedEncodingError, requests.exceptions.ContentDecodingError, OSError) as e:
            last_err = e
            # 연결이 리셋된 경우 기존 keep-alive 연결을 버리기 위해 세션 재생성
            if "Connection reset" in repr(e) or "ConnectionResetError" in repr(e) or "reset by peer" in repr(e):
                global _SESSION
                try:
                    session.close()
                except Exception:
                    pass
                _SESSION = None
                session = get_retry_session()
            continue
    raise RuntimeError(f"HTML 요청 실패: {repr(last_err)} | URL={url}")


def collect_items_from_list_url_requests(list_url: str, max_pages: int = 3, per_page: int = 100, source_keyword: str = "") -> List[ListItem]:
    all_items: List[ListItem] = []
    seen = set()
    referer = BASE_URL + "/"
    for page_no in range(1, max(1, int(max_pages)) + 1):
        url = update_url_page_perpage(list_url, page_no, per_page)
        html = get_html_requests(url, referer=referer)
        referer = url
        items = parse_list_items_from_html(html, url, source_keyword=source_keyword)
        if not items:
            break
        new_count = 0
        for item in items:
            if item.detail_url not in seen:
                seen.add(item.detail_url)
                all_items.append(item)
                new_count += 1
        if new_count == 0:
            break
        time.sleep(random.uniform(0.8, 1.7))
    return all_items


def collect_items_by_keyword_requests(keyword: str, max_pages: int = 3, per_page: int = 100) -> List[ListItem]:
    base = build_keyword_list_url(keyword, page=1, per_page=per_page)
    return collect_items_from_list_url_requests(base, max_pages=max_pages, per_page=per_page, source_keyword=keyword)


def read_detail_html_requests(detail_url: str) -> str:
    candidates = [detail_url]
    m = re.search(r"/(?:data|dataset)/(\d+)/fileData\.do", detail_url)
    if m:
        data_id = m.group(1)
        for u in [f"https://www.data.go.kr/data/{data_id}/fileData.do", f"https://www.data.go.kr/dataset/{data_id}/fileData.do?lang=ko"]:
            if u not in candidates:
                candidates.append(u)
    last_html = ""
    last_err = None
    for url in candidates:
        try:
            html = get_html_requests(url, referer=BASE_URL + "/")
            last_html = html
            if html and "제공기관" in html:
                return html
        except Exception as e:
            last_err = e
            continue
    if last_html:
        return last_html
    if last_err:
        raise last_err
    return ""


# ==========================================================
# Browser helpers (파일 다운로드 / 선택 시만 사용)
# ==========================================================
def find_system_browser_executable() -> str:
    env_path = os.environ.get("CHROME_EXECUTABLE_PATH") or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
    candidates = [env_path] if env_path else []
    for name in ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"]:
        found = shutil.which(name)
        if found:
            candidates.append(found)
    candidates.extend([
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ])
    for p in candidates:
        if p and Path(p).exists():
            return str(p)
    return ""


def ensure_playwright_chromium(auto_install: bool = True) -> Tuple[bool, str]:
    if not auto_install:
        return False, "auto_install=False"
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, out[-4000:]
    except Exception as e:
        return False, repr(e)


def launch_chromium_robust(pw, headless: bool = True, auto_install: bool = True):
    try:
        return pw.chromium.launch(headless=headless)
    except Exception as first:
        first_msg = str(first)
    system_browser = find_system_browser_executable()
    if system_browser:
        try:
            return pw.chromium.launch(headless=headless, executable_path=system_browser)
        except Exception:
            pass
    if "Executable doesn't exist" in first_msg or "playwright install" in first_msg.lower() or "Looks like Playwright" in first_msg:
        ok, install_log = ensure_playwright_chromium(auto_install=auto_install)
        if ok:
            return pw.chromium.launch(headless=headless)
        raise RuntimeError(
            "Playwright 브라우저가 설치되어 있지 않습니다.\n"
            "파일 다운로드 기능을 사용하려면 CMD에서 `python -m playwright install chromium`을 실행하세요.\n"
            f"자동 설치 로그:\n{install_log}\n\n원본 오류:\n{first_msg}"
        )
    raise RuntimeError(f"Playwright 브라우저 실행 실패: {first_msg}")


def setup_context(pw, headless: bool = True):
    browser = launch_chromium_robust(pw, headless=headless, auto_install=True)
    context = browser.new_context(
        locale="ko-KR",
        viewport={"width": 1440, "height": 950},
        user_agent=DEFAULT_UA,
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"},
    )
    def route_handler(route):
        try:
            if route.request.resource_type in {"image", "media", "font"}:
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass
    context.route("**/*", route_handler)
    return browser, context


def collect_items_from_list_url(page, list_url: str, max_pages: int = 3, per_page: int = 100, source_keyword: str = "") -> List[ListItem]:
    all_items: List[ListItem] = []
    seen = set()
    for page_no in range(1, max(1, int(max_pages)) + 1):
        url = update_url_page_perpage(list_url, page_no, per_page)
        page.goto(url, wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_selector("div.result-list ul li, #fileDataList ul li, a[href*='/data/'], a[href*='/dataset/']", timeout=10000)
        except PlaywrightTimeoutError:
            try:
                tab = page.query_selector("a.dtype-tab[data-type='FILE'], a:has-text('파일데이터')")
                if tab:
                    tab.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=10000)
            except Exception:
                pass
        time.sleep(random.uniform(0.3, 0.8))
        items = parse_list_items_from_html(page.content(), page.url, source_keyword=source_keyword)
        if not items:
            break
        new_count = 0
        for item in items:
            if item.detail_url not in seen:
                seen.add(item.detail_url)
                all_items.append(item)
                new_count += 1
        if new_count == 0:
            break
    return all_items


def collect_items_by_keyword(page, keyword: str, max_pages: int = 3, per_page: int = 100) -> List[ListItem]:
    base = build_keyword_list_url(keyword, page=1, per_page=per_page)
    return collect_items_from_list_url(page, base, max_pages=max_pages, per_page=per_page, source_keyword=keyword)


def extract_provider_from_html(html: str, detail_url: str = "") -> Dict[str, str]:
    soup = BeautifulSoup(html or "", "lxml")
    provider_name = ""
    provider_url = ""
    dataset_name = ""
    for table in soup.select("table"):
        table_text = clean_text(table.get_text(" "))
        if "제공기관" not in table_text:
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
                    dataset_name = value
                if key == "제공기관":
                    provider_name = value
                    a = val_cell.select_one("a[href]")
                    if a:
                        provider_url = absolute_url(a.get("href", ""), detail_url or BASE_URL)
                    return {"provider_name": clean_text(provider_name), "provider_url": clean_text(provider_url), "dataset_name": clean_title(dataset_name)}
                i += 2
    text = clean_text(soup.get_text(" "))
    m = re.search(r"제공기관\s*[:：]?\s*(.*?)(?=관리부서|분류체계|등록일|수정일|키워드|파일데이터명|$)", text)
    if m:
        provider_name = clean_text(m.group(1))
    return {"provider_name": provider_name, "provider_url": provider_url, "dataset_name": dataset_name}


def read_detail_html(page, detail_url: str) -> str:
    candidates = [detail_url]
    m = re.search(r"/(?:data|dataset)/(\d+)/fileData\.do", detail_url)
    if m:
        data_id = m.group(1)
        for u in [f"https://www.data.go.kr/data/{data_id}/fileData.do", f"https://www.data.go.kr/dataset/{data_id}/fileData.do?lang=ko"]:
            if u not in candidates:
                candidates.append(u)
    last_html = ""
    last_err = None
    for url in candidates:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
            status = resp.status if resp else None
            try:
                page.wait_for_selector("table, body", timeout=8000)
            except Exception:
                pass
            time.sleep(random.uniform(0.2, 0.5))
            html = page.content()
            last_html = html
            if status not in {403, 429, 500, 502, 503, 504} and html and "제공기관" in html:
                return html
        except Exception as e:
            last_err = e
            continue
    if last_html:
        return last_html
    if last_err:
        raise last_err
    return ""


def _candidate_to_dict(candidate: ProviderCandidate, score_sum: int) -> Dict:
    d = asdict(candidate)
    d["avg_score"] = round(score_sum / max(1, candidate.hit_count), 1)
    return d


def find_provider_candidates(
    keyword: str,
    max_pages: int = 3,
    max_detail_check: int = 30,
    headless: bool = True,
    use_browser: bool = False,
    score_threshold: int = DEFAULT_SCORE_THRESHOLD,
) -> Dict:
    """기관명 일부/검색어 입력 → 상세페이지 제공기관 기준 후보 목록 반환.
    80점 이상 후보만 반환한다.
    """
    keyword = clean_text(keyword)
    if not keyword:
        raise ValueError("검색어를 입력하세요.")
    log: List[Dict] = []
    candidates: Dict[str, ProviderCandidate] = {}
    score_sums: Dict[str, int] = {}
    raw_items: List[ListItem] = []

    def handle_item(item: ListItem, html: str, mode_label: str):
        info = extract_provider_from_html(html, item.detail_url)
        provider = clean_text(info.get("provider_name", ""))
        if not provider:
            log.append({"step": f"provider_extract_{mode_label}", "detail_url": item.detail_url, "ok": False, "reason": "NO_PROVIDER"})
            return
        score = provider_similarity_score(keyword, provider)
        if score < int(score_threshold):
            log.append({"step": f"provider_score_{mode_label}", "detail_url": item.detail_url, "provider": provider, "score": score, "passed": False})
            return
        key = norm_org(provider)
        if key not in candidates:
            candidates[key] = ProviderCandidate(provider_name=provider, provider_url=info.get("provider_url", ""), sample_detail_url=item.detail_url, sample_title=item.title or info.get("dataset_name", ""), hit_count=1, max_score=score, avg_score=float(score))
            score_sums[key] = score
        else:
            candidates[key].hit_count += 1
            candidates[key].max_score = max(candidates[key].max_score, score)
            score_sums[key] = score_sums.get(key, 0) + score
            if not candidates[key].provider_url and info.get("provider_url"):
                candidates[key].provider_url = info.get("provider_url", "")
        log.append({"step": f"provider_score_{mode_label}", "detail_url": item.detail_url, "provider": provider, "score": score, "passed": True})

    if not use_browser:
        raw_items = collect_items_by_keyword_requests(keyword, max_pages=max_pages, per_page=100)
        log.append({"step": "keyword_list_collect_requests", "keyword": keyword, "items": len(raw_items), "mode": "requests", "score_threshold": score_threshold})
        for item in raw_items[: max(1, int(max_detail_check))]:
            try:
                handle_item(item, read_detail_html_requests(item.detail_url), "requests")
            except Exception as e:
                log.append({"step": "provider_extract_requests", "detail_url": item.detail_url, "ok": False, "error": repr(e)})
    else:
        with sync_playwright() as pw:
            browser, context = setup_context(pw, headless=headless)
            page = context.new_page()
            detail_page = context.new_page()
            try:
                raw_items = collect_items_by_keyword(page, keyword, max_pages=max_pages, per_page=100)
                log.append({"step": "keyword_list_collect_browser", "keyword": keyword, "items": len(raw_items), "page_url": page.url, "mode": "browser", "score_threshold": score_threshold})
                for item in raw_items[: max(1, int(max_detail_check))]:
                    try:
                        handle_item(item, read_detail_html(detail_page, item.detail_url), "browser")
                    except Exception as e:
                        log.append({"step": "provider_extract_browser", "detail_url": item.detail_url, "ok": False, "error": repr(e)})
            finally:
                context.close()
                browser.close()

    candidate_list = [_candidate_to_dict(c, score_sums.get(k, c.max_score)) for k, c in candidates.items()]
    candidate_list = sorted(candidate_list, key=lambda x: (-int(x.get("max_score", 0)), -int(x.get("hit_count", 0)), x.get("provider_name", "")))
    return {"input_keyword": keyword, "list_items_checked": len(raw_items), "score_threshold": int(score_threshold), "candidates": candidate_list, "log": log}


def unique_keywords(seed_keyword: str, provider_name: str) -> List[str]:
    values = [seed_keyword, provider_name, strip_company_marker(provider_name)]
    base = strip_company_marker(provider_name)
    if base.startswith("한국") and len(base) > 2:
        values.append(base[2:])
    out = []
    for v in values:
        v = clean_text(v)
        if v and v not in out:
            out.append(v)
    return out


def resolve_provider_filedata_items(
    provider_name: str,
    seed_keyword: str,
    provider_url: str = "",
    max_pages: int = 5,
    max_items: int = 0,
    headless: bool = True,
    use_browser: bool = False,
    score_threshold: int = DEFAULT_SCORE_THRESHOLD,
) -> Dict:
    """선택 기관 기준으로 상세 URL을 수집하고 제공기관 유사도 점수로 필터링한다.
    URL '검증'이라는 별도 단계가 아니라 80점 이상 데이터만 수집대상으로 확정한다.
    """
    provider_name = clean_text(provider_name)
    seed_keyword = clean_text(seed_keyword)
    if not provider_name:
        raise ValueError("선택 기관명이 없습니다.")
    log: List[Dict] = []
    candidate_items: Dict[str, ListItem] = {}
    verified: List[VerifiedItem] = []

    def add_items(items: List[ListItem]):
        for item in items:
            candidate_items.setdefault(item.detail_url, item)

    if not use_browser:
        if provider_url:
            try:
                items = collect_items_from_list_url_requests(provider_url, max_pages=max_pages, per_page=100, source_keyword="provider_official_link")
                add_items(items)
                log.append({"step": "provider_url_collect_requests", "url": provider_url, "items": len(items), "ok": True})
            except Exception as e:
                log.append({"step": "provider_url_collect_requests", "url": provider_url, "items": 0, "ok": False, "error": repr(e)})
        for kw in unique_keywords(seed_keyword, provider_name):
            try:
                items = collect_items_by_keyword_requests(kw, max_pages=max_pages, per_page=100)
                add_items(items)
                log.append({"step": "keyword_collect_requests", "keyword": kw, "items": len(items), "ok": True})
            except Exception as e:
                log.append({"step": "keyword_collect_requests", "keyword": kw, "items": 0, "ok": False, "error": repr(e)})
        for item in candidate_items.values():
            if max_items and len(verified) >= int(max_items):
                break
            try:
                info = extract_provider_from_html(read_detail_html_requests(item.detail_url), item.detail_url)
                actual_provider = clean_text(info.get("provider_name", ""))
                score = provider_similarity_score(provider_name, actual_provider)
                passed = score >= int(score_threshold)
                log.append({"step": "detail_score_filter_requests", "detail_url": item.detail_url, "title": item.title, "selected_provider": provider_name, "actual_provider": actual_provider, "score": score, "passed": passed})
                if passed:
                    verified.append(VerifiedItem(title=item.title or info.get("dataset_name", ""), detail_url=item.detail_url, provider_name=actual_provider, provider_url=info.get("provider_url", "") or provider_url, view_count=item.view_count, download_count=item.download_count, source_list_url=item.source_list_url, source_keyword=item.source_keyword, provider_score=score))
            except Exception as e:
                log.append({"step": "detail_score_filter_requests", "detail_url": item.detail_url, "passed": False, "error": repr(e)})
    else:
        with sync_playwright() as pw:
            browser, context = setup_context(pw, headless=headless)
            list_page = context.new_page()
            detail_page = context.new_page()
            try:
                if provider_url:
                    try:
                        items = collect_items_from_list_url(list_page, provider_url, max_pages=max_pages, per_page=100, source_keyword="provider_official_link")
                        add_items(items)
                        log.append({"step": "provider_url_collect_browser", "url": provider_url, "items": len(items), "ok": True})
                    except Exception as e:
                        log.append({"step": "provider_url_collect_browser", "url": provider_url, "items": 0, "ok": False, "error": repr(e)})
                for kw in unique_keywords(seed_keyword, provider_name):
                    try:
                        items = collect_items_by_keyword(list_page, kw, max_pages=max_pages, per_page=100)
                        add_items(items)
                        log.append({"step": "keyword_collect_browser", "keyword": kw, "items": len(items), "ok": True})
                    except Exception as e:
                        log.append({"step": "keyword_collect_browser", "keyword": kw, "items": 0, "ok": False, "error": repr(e)})
                for item in candidate_items.values():
                    if max_items and len(verified) >= int(max_items):
                        break
                    try:
                        info = extract_provider_from_html(read_detail_html(detail_page, item.detail_url), item.detail_url)
                        actual_provider = clean_text(info.get("provider_name", ""))
                        score = provider_similarity_score(provider_name, actual_provider)
                        passed = score >= int(score_threshold)
                        log.append({"step": "detail_score_filter_browser", "detail_url": item.detail_url, "title": item.title, "selected_provider": provider_name, "actual_provider": actual_provider, "score": score, "passed": passed})
                        if passed:
                            verified.append(VerifiedItem(title=item.title or info.get("dataset_name", ""), detail_url=item.detail_url, provider_name=actual_provider, provider_url=info.get("provider_url", "") or provider_url, view_count=item.view_count, download_count=item.download_count, source_list_url=item.source_list_url, source_keyword=item.source_keyword, provider_score=score))
                    except Exception as e:
                        log.append({"step": "detail_score_filter_browser", "detail_url": item.detail_url, "passed": False, "error": repr(e)})
            finally:
                context.close()
                browser.close()

    return {
        "selected_provider": provider_name,
        "seed_keyword": seed_keyword,
        "provider_url": provider_url,
        "resolver_method": "keyword_search_then_provider_similarity_score_filter",
        "resolver_mode": "browser" if use_browser else "requests",
        "score_threshold": int(score_threshold),
        "candidate_detail_url_count": len(candidate_items),
        "verified_detail_url_count": len(verified),
        "detail_items": [asdict(v) for v in verified],
        "log": log,
    }


def save_resolution(resolution: Dict, output_dir: str = "outputs/resolution") -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    provider = clean_text(resolution.get("selected_provider", "기관")) or "기관"
    safe = re.sub(r"[\\/:*?\"<>|]", "_", provider)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"resolution_{safe}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resolution, f, ensure_ascii=False, indent=2)
    return str(path)
