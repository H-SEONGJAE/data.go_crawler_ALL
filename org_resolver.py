# -*- coding: utf-8 -*-
"""
org_resolver.py

공공데이터포털 기관 검색 → 실제 파일데이터 목록 화면/상세 URL 확보 모듈.

핵심 원칙
- 기관명으로 URL을 직접 조립하지 않는다.
- Playwright로 포털 화면에서 실제 검색/탭 이동/목록 검증을 수행한다.
- 선택 기관의 상세 URL 샘플을 열어 제공기관 메타데이터까지 교차 검증한다.
- 전체 크롤러에는 검증된 resolved_url 또는 detail_items manifest를 전달한다.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.data.go.kr"
DATASET_LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"


# ==========================================================
# 데이터 구조
# ==========================================================
@dataclass
class ResolvedItem:
    raw_title: str = ""
    title: str = ""
    title_source: str = "resolver"
    확장자: str = ""
    조회수: str = ""
    다운로드_바로가기: str = ""
    다운로드수: str = ""
    detail_url: str = ""
    source_list_url: str = ""
    provider: str = ""

    def to_crawler_metadata_item(self) -> Dict[str, Any]:
        # crawler_metadata.py 내부 item 스키마와 맞춘다.
        return {
            "raw_title": self.raw_title,
            "title": self.title or self.raw_title,
            "title_source": self.title_source,
            "확장자": self.확장자,
            "조회수": self.조회수,
            "다운로드(바로가기)": self.다운로드_바로가기 or self.다운로드수,
            "다운로드수": self.다운로드수 or self.다운로드_바로가기,
            "detail_url": self.detail_url,
            "source_list_url": self.source_list_url,
        }


@dataclass
class OrgCandidate:
    provider: str
    count_on_scanned_pages: int = 0
    sample_title: str = ""
    sample_detail_url: str = ""
    provider_url_from_detail: str = ""


@dataclass
class OrgResolution:
    input_keyword: str
    selected_provider: str
    resolved_url: str
    provider_url_from_detail: str = ""
    validation_status: str = ""
    validation_messages: List[str] = field(default_factory=list)
    first_page_count: int = 0
    total_items_collected: int = 0
    sample_detail_urls: List[str] = field(default_factory=list)
    detail_items: List[Dict[str, Any]] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)


# ==========================================================
# 공통 유틸
# ==========================================================
def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact(value: str) -> str:
    return re.sub(r"[\s\(\)\[\]\{\}㈜주식회사_\-·ㆍ,./]", "", clean_text(value)).lower()


def provider_matches(left: str, right: str) -> bool:
    a, b = compact(left), compact(right)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def safe_abs_url(href: str, base: str = BASE_URL) -> str:
    href = clean_text(href)
    if not href:
        return ""
    return urljoin(base, href)


def is_detail_url(url: str) -> bool:
    u = clean_text(url).lower()
    return bool(
        u.startswith("http")
        and ("/data/" in u or "/dataset/" in u)
        and ("filedata.do" in u or re.search(r"/(?:data|dataset)/\d+", u))
    )


def normalize_detail_url(url: str) -> str:
    url = clean_text(url)
    # data.go.kr 상세 URL은 query가 붙어도 같은 데이터인 경우가 많지만, lang 등은 보존한다.
    return url


def clean_dataset_title(raw_title: str) -> str:
    s = clean_text(raw_title)
    if not s:
        return ""

    file_type_pattern = (
        r"CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|"
        r"MP4|AVI|MOV|WMV|JPG|JPEG|PNG|GIF|DOCX|DOC|PPTX|PPT|"
        r"파일데이터|오픈API|API"
    )
    s = re.sub(r"^\s*MP\s*,\s*MP4\s+", "", s, flags=re.I)
    s = re.sub(rf"^((?:{file_type_pattern})\s*(?:\+|,|/|\\|｜|·|ㆍ|-)?\s*)+", "", s, flags=re.I)
    s = re.sub(r"\b(New|Update|Updated|업데이트)\b", "", s, flags=re.I)
    cut_markers = ["제공기관", "분류체계", "수정일", "등록일", "조회수", "다운로드", "키워드"]
    cut_positions = []
    for marker in cut_markers:
        m = re.search(rf"\s+{re.escape(marker)}\s*[:：]?\s+", s)
        if m and m.start() > 0:
            cut_positions.append(m.start())
    if cut_positions:
        s = s[:min(cut_positions)]
    s = re.sub(r"\s*[|｜-]\s*공공데이터포털\s*$", "", s)
    return clean_text(s)


def parse_count_pair(text: str) -> Tuple[str, str]:
    text = clean_text(text)
    view, download = "", ""
    pair_patterns = [
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드수?\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*수\s*[:：]?\s*([0-9][0-9,]*)",
    ]
    for pat in pair_patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            return m.group(1).replace(",", ""), m.group(2).replace(",", "")

    m = re.search(r"조회\s*수?\s*[:：]?\s*([0-9][0-9,]*)", text, flags=re.I)
    if m:
        view = m.group(1).replace(",", "")
    m = re.search(r"다운로드\s*수?\s*[:：]?\s*([0-9][0-9,]*)", text, flags=re.I)
    if m:
        download = m.group(1).replace(",", "")
    return view, download


def parse_provider_from_card_text(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""
    labels = ["분류체계", "등록일", "수정일", "조회수", "조회 수", "다운로드", "다운로드수", "키워드"]
    stop = "|".join(re.escape(x) for x in labels)
    m = re.search(rf"제공기관\s*[:：]?\s*(.*?)(?=({stop})\s*[:：]?|$)", text)
    if not m:
        return ""
    provider = clean_text(m.group(1))
    if len(provider) > 80:
        return ""
    return provider


def extract_title_from_anchor(a) -> str:
    candidates = []
    for attr in ["title", "data-title", "aria-label", "data-nm", "data-name"]:
        try:
            v = clean_text(a.get_attribute(attr))
        except Exception:
            v = ""
        if v:
            candidates.append(v)
    try:
        txt = clean_text(a.inner_text())
        if txt:
            candidates.append(txt)
    except Exception:
        pass
    candidates = [clean_dataset_title(x) for x in candidates if clean_dataset_title(x)]
    if not candidates:
        return ""
    # 가장 길고 '_'가 있는 제목 선호
    return sorted(candidates, key=lambda x: (("_" in x), len(x)), reverse=True)[0]


# ==========================================================
# 페이지 조작/추출
# ==========================================================
def make_browser_context(pw, headless: bool = True):
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(
        locale="ko-KR",
        viewport={"width": 1440, "height": 960},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"},
    )
    return browser, context


def wait_soft(page, sec: float = 0.5):
    try:
        page.wait_for_load_state("networkidle", timeout=7000)
    except Exception:
        pass
    time.sleep(sec)


def has_file_list(page) -> bool:
    try:
        return bool(page.query_selector_all("div.result-list ul li, #fileDataList ul li"))
    except Exception:
        return False


def ensure_file_tab_or_list(page, timeout_ms: int = 15000) -> None:
    """현재 화면이 파일데이터 목록이면 그대로 두고, 아니면 파일데이터 탭을 클릭한다."""
    if has_file_list(page):
        return

    selectors = [
        "a.dtype-tab[data-type='FILE']",
        "li#dTypeFILE a",
        "a[href*='dType=FILE']",
        "button:has-text('파일데이터')",
        "a:has-text('파일데이터')",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el:
                page.evaluate("el => el.click()", el)
                wait_soft(page, 1.0)
                if has_file_list(page):
                    return
        except Exception:
            continue

    # 마지막 대기
    try:
        page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass


def submit_keyword_search(page, keyword: str) -> None:
    """검색 입력창 selector가 바뀌어도 견디도록 후보 selector를 순차 시도한다."""
    keyword = clean_text(keyword)
    page.goto(DATASET_LIST_URL, wait_until="domcontentloaded", timeout=20000)
    wait_soft(page, 1.0)

    # 먼저 파일데이터 탭을 열어 둔다. 실패해도 검색 이후 다시 확인한다.
    try:
        ensure_file_tab_or_list(page, timeout_ms=4000)
    except Exception:
        pass

    input_selectors = [
        "input[name='keyword']",
        "input#keyword",
        "input[name='searchKeyword']",
        "input[placeholder*='검색']",
        "input[title*='검색']",
        "input[type='text']",
    ]

    filled = False
    for sel in input_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.fill(keyword)
                filled = True
                break
        except Exception:
            continue

    if filled:
        clicked = False
        button_selectors = [
            "button:has-text('검색')",
            "a:has-text('검색')",
            "input[type='submit']",
            "button[type='submit']",
            ".btn-search",
            "#btnSearch",
        ]
        for sel in button_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    page.evaluate("el => el.click()", btn)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            page.keyboard.press("Enter")
        wait_soft(page, 1.5)
    else:
        # 입력창을 못 찾으면 keyword 파라미터 검색으로 보조 진입만 한다.
        # 최종 URL 표준으로 사용하지 않고, 후보 목록을 얻기 위한 fallback이다.
        fallback = f"{DATASET_LIST_URL}?keyword={keyword}&dType=FILE"
        page.goto(fallback, wait_until="domcontentloaded", timeout=20000)
        wait_soft(page, 1.5)

    ensure_file_tab_or_list(page, timeout_ms=10000)


def extract_items_on_current_page(page, source_url: Optional[str] = None) -> List[ResolvedItem]:
    source_url = source_url or page.url
    items: List[ResolvedItem] = []
    seen = set()

    li_list = page.query_selector_all("div.result-list ul li, #fileDataList ul li")
    for li in li_list:
        try:
            text = clean_text(li.inner_text())
        except Exception:
            text = ""

        anchors = li.query_selector_all("a[href*='/data/'], a[href*='/dataset/']")
        detail_url = ""
        title = ""
        for a in anchors:
            href = safe_abs_url(a.get_attribute("href") or "", page.url)
            if is_detail_url(href):
                detail_url = normalize_detail_url(href)
                title = extract_title_from_anchor(a)
                break
        if not detail_url or detail_url in seen:
            continue
        seen.add(detail_url)

        title_el = li.query_selector("span.title, .title")
        if title_el:
            try:
                title_from_el = clean_dataset_title(title_el.get_attribute("title") or title_el.inner_text())
                if len(title_from_el) >= len(title):
                    title = title_from_el
            except Exception:
                pass

        provider = parse_provider_from_card_text(text)
        view, down = parse_count_pair(text)

        items.append(ResolvedItem(
            raw_title=title,
            title=title,
            title_source="resolver_list",
            조회수=view,
            다운로드_바로가기=down,
            다운로드수=down,
            detail_url=detail_url,
            source_list_url=source_url,
            provider=provider,
        ))
    return items


def goto_next_page(page) -> bool:
    """공공데이터포털 목록 pagination을 최대한 안전하게 다음 페이지로 넘긴다."""
    try:
        curr = page.query_selector("nav.pagination strong.active, .pagination strong.active, nav.pagination strong")
        if curr:
            next_el = curr.evaluate_handle("node => node.nextElementSibling")
            if next_el:
                tag = str(next_el.get_property("tagName").json_value()).lower()
                if tag == "a":
                    page.evaluate("el => el.click()", next_el)
                    wait_soft(page, 0.9)
                    return True
    except Exception:
        pass

    for sel in ["a.control.next", "nav.pagination a.next", "a:has-text('다음')", "a[title*='다음']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                # 비활성 next 방지
                cls = clean_text(el.get_attribute("class"))
                if "disabled" in cls or "off" in cls:
                    continue
                page.evaluate("el => el.click()", el)
                wait_soft(page, 1.0)
                return True
        except Exception:
            continue
    return False


def extract_provider_info_from_detail(page, detail_url: str) -> Tuple[str, str, bool, str]:
    """상세페이지에서 제공기관명/제공기관 링크/메타테이블 확인 여부를 추출한다."""
    page.goto(detail_url, wait_until="domcontentloaded", timeout=20000)
    wait_soft(page, 1.0)
    html = page.content()
    soup = BeautifulSoup(html, "lxml")

    provider_name = ""
    provider_url = ""
    has_meta_table = False

    for table in soup.select("table"):
        text = clean_text(table.get_text(" "))
        if "파일데이터명" in text or ("제공기관" in text and "분류체계" in text):
            has_meta_table = True
        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False) or tr.find_all(["th", "td"])
            for i in range(len(cells) - 1):
                key = clean_text(cells[i].get_text(" "))
                if "제공기관" not in key:
                    continue
                val_cell = cells[i + 1]
                provider_name = clean_text(val_cell.get_text(" "))
                a = val_cell.select_one("a[href]")
                if a:
                    provider_url = safe_abs_url(a.get("href"), detail_url)
                break
            if provider_name:
                break
        if provider_name:
            break

    if not provider_name:
        # 본문 라벨 fallback
        text = clean_text(soup.get_text(" "))
        m = re.search(r"제공기관\s*[:：]?\s*(.*?)(?=분류체계|관리부서|등록일|수정일|$)", text)
        if m:
            provider_name = clean_text(m.group(1))[:80]

    title = ""
    for sel in ["h1", "h2", "h3", ".title", "title"]:
        el = soup.select_one(sel)
        if el:
            title = clean_dataset_title(el.get_text(" "))
            if title:
                break
    return provider_name, provider_url, has_meta_table, title


def collect_list_items_across_pages(page, max_pages: int = 3, provider_filter: str = "") -> List[ResolvedItem]:
    all_items: List[ResolvedItem] = []
    seen = set()
    for page_no in range(1, max_pages + 1):
        try:
            page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=12000)
        except Exception:
            break
        page_items = extract_items_on_current_page(page)
        for item in page_items:
            if provider_filter and item.provider and not provider_matches(item.provider, provider_filter):
                # provider_url로 들어온 경우 카드 provider 추출이 없을 수 있으므로 provider 값이 있을 때만 필터
                continue
            if item.detail_url not in seen:
                seen.add(item.detail_url)
                all_items.append(item)
        if not goto_next_page(page):
            break
    return all_items


# ==========================================================
# 외부 API: 후보 검색/기관 선택 검증
# ==========================================================
def search_org_candidates(keyword: str, max_scan_pages: int = 3, headless: bool = True) -> Dict[str, Any]:
    """
    풀네임을 몰라도 기관 후보를 보여주기 위한 검색 함수.
    - keyword로 포털 실제 검색 수행
    - 첫 N페이지 카드에서 제공기관명을 모아 후보 생성
    - 카드에서 제공기관을 못 읽으면 상세페이지 샘플을 열어 제공기관명을 보강
    """
    keyword = clean_text(keyword)
    if not keyword:
        raise ValueError("검색어가 비어 있습니다.")

    candidates: Dict[str, OrgCandidate] = {}
    scanned_items: List[ResolvedItem] = []
    messages: List[str] = []

    with sync_playwright() as pw:
        browser, context = make_browser_context(pw, headless=headless)
        page = context.new_page()
        try:
            submit_keyword_search(page, keyword)
            scanned_items = collect_list_items_across_pages(page, max_pages=max_scan_pages)
            messages.append(f"목록 화면에서 {len(scanned_items)}건의 상세 URL 후보를 확인했습니다.")

            # 카드 provider가 있으면 우선 그룹핑
            for item in scanned_items:
                if not item.provider:
                    continue
                key = item.provider
                if key not in candidates:
                    candidates[key] = OrgCandidate(provider=key, sample_title=item.title, sample_detail_url=item.detail_url)
                candidates[key].count_on_scanned_pages += 1

            # provider가 적게 잡힌 경우 상세페이지 샘플로 보강
            need_detail = [x for x in scanned_items if not x.provider][: min(10, len(scanned_items))]
            for item in need_detail:
                try:
                    provider, provider_url, has_meta, detail_title = extract_provider_info_from_detail(page, item.detail_url)
                    if provider:
                        item.provider = provider
                        if detail_title and (not item.title or len(detail_title) > len(item.title)):
                            item.title = detail_title
                        if provider not in candidates:
                            candidates[provider] = OrgCandidate(
                                provider=provider,
                                sample_title=item.title,
                                sample_detail_url=item.detail_url,
                                provider_url_from_detail=provider_url,
                            )
                        candidates[provider].count_on_scanned_pages += 1
                        if provider_url and not candidates[provider].provider_url_from_detail:
                            candidates[provider].provider_url_from_detail = provider_url
                except Exception as e:
                    messages.append(f"상세 샘플 provider 보강 실패: {repr(e)}")

            candidate_list = sorted(
                [asdict(c) for c in candidates.values()],
                key=lambda x: (x.get("count_on_scanned_pages", 0), len(x.get("provider", ""))),
                reverse=True,
            )
            return {
                "input_keyword": keyword,
                "current_url_after_search": page.url,
                "scanned_items": [asdict(x) for x in scanned_items],
                "candidates": candidate_list,
                "messages": messages,
            }
        finally:
            context.close()
            browser.close()


def resolve_org_filedata(
    keyword: str,
    selected_provider: str,
    max_pages: int = 90,
    headless: bool = True,
    seed_detail_url: str = "",
    provider_url_hint: str = "",
) -> OrgResolution:
    """
    선택한 기관의 파일데이터 목록 URL/상세 URL 목록을 교차 검증하여 반환한다.

    교차 검증
    1) 선택 기관 후보의 상세페이지에서 제공기관 링크(provider_url)를 우선 확보
    2) provider_url 또는 검색 결과 화면에서 파일데이터 목록 li 존재 확인
    3) 상세 URL 샘플을 다시 열어 제공기관명과 메타테이블 확인
    4) 전체 페이지의 detail_items manifest 생성
    """
    keyword = clean_text(keyword)
    selected_provider = clean_text(selected_provider)
    messages: List[str] = []
    provider_url_from_detail = clean_text(provider_url_hint)

    if not selected_provider:
        raise ValueError("selected_provider가 비어 있습니다.")

    with sync_playwright() as pw:
        browser, context = make_browser_context(pw, headless=headless)
        page = context.new_page()
        try:
            # 1) provider_url 확보: 후보 상세 URL을 우선 열어 제공기관 링크를 찾는다.
            if seed_detail_url:
                try:
                    p_name, p_url, has_meta, d_title = extract_provider_info_from_detail(page, seed_detail_url)
                    if p_url:
                        provider_url_from_detail = p_url
                        messages.append("상세페이지의 제공기관 링크를 확보했습니다.")
                    if p_name and not provider_matches(p_name, selected_provider):
                        messages.append(f"주의: 샘플 상세 제공기관({p_name})과 선택 기관({selected_provider})이 완전히 같지 않습니다.")
                except Exception as e:
                    messages.append(f"제공기관 링크 확보 실패: {repr(e)}")

            # 2) provider_url이 있으면 그 URL로 진입. 없으면 실제 검색 화면으로 진입한다.
            if provider_url_from_detail:
                page.goto(provider_url_from_detail, wait_until="domcontentloaded", timeout=20000)
                wait_soft(page, 1.0)
                ensure_file_tab_or_list(page, timeout_ms=12000)
                messages.append("제공기관 링크 기준으로 파일데이터 목록 진입을 시도했습니다.")
            else:
                submit_keyword_search(page, keyword or selected_provider)
                messages.append("제공기관 링크가 없어 검색 결과 화면 기준으로 목록 진입을 시도했습니다.")

            if not has_file_list(page):
                raise RuntimeError("파일데이터 목록을 찾지 못했습니다. 검색/기관 선택 결과를 확인해야 합니다.")

            resolved_url = page.url
            first_page_items = extract_items_on_current_page(page, source_url=resolved_url)
            first_page_count = len(first_page_items)
            if first_page_count == 0:
                raise RuntimeError("목록 li는 있으나 상세 URL을 추출하지 못했습니다.")
            messages.append(f"파일데이터 첫 페이지에서 상세 URL {first_page_count}건을 확인했습니다.")

            # 3) 샘플 상세 URL 교차 검증
            sample_detail_urls = [x.detail_url for x in first_page_items[:5]]
            verified_provider_count = 0
            verified_meta_count = 0
            for url in sample_detail_urls[:3]:
                try:
                    p_name, p_url, has_meta, d_title = extract_provider_info_from_detail(page, url)
                    if has_meta:
                        verified_meta_count += 1
                    if p_name and provider_matches(p_name, selected_provider):
                        verified_provider_count += 1
                    elif p_name:
                        messages.append(f"샘플 상세 provider 불일치 가능성: {p_name} / URL={url}")
                except Exception as e:
                    messages.append(f"샘플 상세 검증 실패: {repr(e)} / URL={url}")

            if verified_meta_count == 0:
                raise RuntimeError("상세페이지 메타데이터 테이블 검증에 실패했습니다.")

            if verified_provider_count == 0 and provider_url_from_detail:
                # provider_url 기반이면 목록 자체가 provider로 제한되므로 경고만 둔다.
                messages.append("상세 provider명 직접 일치 검증은 0건입니다. 단, 제공기관 링크 기준 URL로 진입했습니다.")
            elif verified_provider_count == 0:
                raise RuntimeError("선택 기관과 상세페이지 제공기관명이 일치하는 샘플을 찾지 못했습니다.")

            # 4) 전체 페이지 items 수집
            # 샘플 검증 때문에 상세페이지로 이동했으므로 목록 URL로 다시 복귀한다.
            page.goto(resolved_url, wait_until="domcontentloaded", timeout=20000)
            wait_soft(page, 1.0)
            ensure_file_tab_or_list(page, timeout_ms=12000)
            detail_items = collect_list_items_across_pages(
                page,
                max_pages=max_pages,
                provider_filter=("" if provider_url_from_detail else selected_provider),
            )
            messages.append(f"전체 순회 결과 상세 URL {len(detail_items)}건을 확보했습니다.")

            if not detail_items:
                raise RuntimeError("전체 순회 결과 상세 URL 목록이 비어 있습니다.")

            validation_status = "OK"
            return OrgResolution(
                input_keyword=keyword,
                selected_provider=selected_provider,
                resolved_url=resolved_url,
                provider_url_from_detail=provider_url_from_detail,
                validation_status=validation_status,
                validation_messages=messages,
                first_page_count=first_page_count,
                total_items_collected=len(detail_items),
                sample_detail_urls=[x.detail_url for x in detail_items[:5]],
                detail_items=[x.to_crawler_metadata_item() | {"provider": x.provider} for x in detail_items],
            )
        finally:
            context.close()
            browser.close()


def load_resolution(path: str | Path) -> OrgResolution:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return OrgResolution(**data)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="기관 검색/파일데이터 URL Resolver")
    parser.add_argument("--keyword", required=True)
    parser.add_argument("--provider", default="")
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--output", default="resolver_result.json")
    args = parser.parse_args()

    if args.provider:
        result = resolve_org_filedata(
            keyword=args.keyword,
            selected_provider=args.provider,
            max_pages=args.max_pages,
            headless=args.headless.lower() == "true",
        )
        Path(args.output).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = search_org_candidates(args.keyword, max_scan_pages=args.max_pages, headless=args.headless.lower() == "true")
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"저장 완료: {args.output}")
