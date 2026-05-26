# -*- coding: utf-8 -*-
"""
org_url_resolver.py

기관명 일부 입력만으로 공공데이터포털 파일데이터 상세 URL을 안정적으로 확보하는 Resolver.

핵심 원칙
- org=기관명 URL을 직접 조립하지 않는다.
- keyword 검색 또는 상세페이지 내 제공기관 공식 링크에서 목록 후보를 모은다.
- 상세페이지 메타데이터 table의 '제공기관' 값을 다시 읽어 선택 기관과 교차 검증한다.
- 최종 수집은 검증 통과한 detail_items만 사용한다.
"""

from __future__ import annotations

import json
import re
import time
import random
import urllib.parse
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.data.go.kr"
LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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
    validation_status: str = "MATCHED"


def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def norm_org(value: str) -> str:
    s = clean_text(value).lower()
    # 기관명 비교 시 흔히 달라지는 표기 정규화
    replacements = {
        "㈜": "주",
        "(주)": "주",
        "（주）": "주",
        "주식회사": "주",
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


def org_matches(selected_provider: str, actual_provider: str) -> bool:
    a = norm_org(selected_provider)
    b = norm_org(actual_provider)
    if not a or not b:
        return False
    return a == b or a in b or b in a


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
    """기관명이 아니라 일반 검색어 keyword로 파일데이터 검색 URL을 만든다."""
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
    # 가능하면 파일데이터 탭을 명시. 단, 기존 query를 덮어쓰기 때문에 공식 제공기관 링크의 나머지 파라미터는 유지된다.
    q.setdefault("dType", "FILE")
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))


def extract_count_from_text(text: str, labels: List[str]) -> str:
    text = clean_text(text)
    for label in labels:
        # 조회수 1,234 / 조회 수 1,234 / 다운로드 55 대응
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
    # 목록 카드의 포맷 배지 제거
    s = re.sub(
        r"^((CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|API|오픈API)\s*(\+|,|/|-)?\s*)+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # 목록 하단 부가정보 절단
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
    # 긴 제목 우선, 말줄임표 없는 값 우선
    return sorted(cleaned, key=lambda x: (("..." not in x and "…" not in x), len(x)), reverse=True)[0]


def parse_list_items_from_html(html: str, page_url: str, source_keyword: str = "") -> List[ListItem]:
    soup = BeautifulSoup(html or "", "lxml")
    results: List[ListItem] = []
    seen = set()

    containers = soup.select("div.result-list ul li, #fileDataList ul li")
    if not containers:
        # fallback: 상세 URL anchor만이라도 확보
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
        if not is_detail_url(href):
            continue
        if href in seen:
            continue
        seen.add(href)

        title = extract_title_from_anchor(a) or clean_title(text)
        view, download = extract_view_download(text)
        results.append(
            ListItem(
                title=title,
                detail_url=href,
                view_count=view,
                download_count=download,
                source_list_url=page_url,
                source_keyword=source_keyword,
            )
        )
    return results


def setup_context(pw, headless: bool = True):
    browser = pw.chromium.launch(headless=headless)
    context = browser.new_context(
        locale="ko-KR",
        viewport={"width": 1440, "height": 950},
        user_agent=DEFAULT_UA,
        extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8"},
    )
    # 이미지/폰트/미디어 차단. CSS는 UI 구조 확인을 위해 유지.
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
            # 파일데이터 탭이 분리된 화면이면 클릭 시도
            try:
                tab = page.query_selector("a.dtype-tab[data-type='FILE'], a:has-text('파일데이터')")
                if tab:
                    tab.click()
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=10000)
            except Exception:
                pass
        time.sleep(random.uniform(0.3, 0.8))
        html = page.content()
        items = parse_list_items_from_html(html, page.url, source_keyword=source_keyword)
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

    # 상세 메타데이터 table의 th/td pair를 우선 사용
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
                    return {
                        "provider_name": clean_text(provider_name),
                        "provider_url": clean_text(provider_url),
                        "dataset_name": clean_title(dataset_name),
                    }
                i += 2

    # fallback: 텍스트 블록에서 제공기관 다음 라벨 전까지 추출
    text = clean_text(soup.get_text(" "))
    m = re.search(r"제공기관\s*[:：]?\s*(.*?)(?=관리부서|분류체계|등록일|수정일|키워드|파일데이터명|$)", text)
    if m:
        provider_name = clean_text(m.group(1))
    return {"provider_name": provider_name, "provider_url": provider_url, "dataset_name": dataset_name}


def read_detail_html(page, detail_url: str) -> str:
    # /data/ 와 /dataset/ 형태 후보를 모두 시도
    candidates = [detail_url]
    m = re.search(r"/(?:data|dataset)/(\d+)/fileData\.do", detail_url)
    if m:
        data_id = m.group(1)
        for u in [
            f"https://www.data.go.kr/data/{data_id}/fileData.do",
            f"https://www.data.go.kr/dataset/{data_id}/fileData.do?lang=ko",
        ]:
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


def find_provider_candidates(
    keyword: str,
    max_pages: int = 3,
    max_detail_check: int = 30,
    headless: bool = True,
) -> Dict:
    """기관명 일부/검색어 입력 → 상세페이지 제공기관 기준 후보 목록 반환."""
    keyword = clean_text(keyword)
    if not keyword:
        raise ValueError("검색어를 입력하세요.")

    log: List[Dict] = []
    candidates: Dict[str, ProviderCandidate] = {}
    raw_items: List[ListItem] = []

    with sync_playwright() as pw:
        browser, context = setup_context(pw, headless=headless)
        page = context.new_page()
        detail_page = context.new_page()
        try:
            raw_items = collect_items_by_keyword(page, keyword, max_pages=max_pages, per_page=100)
            log.append({"step": "keyword_list_collect", "keyword": keyword, "items": len(raw_items), "page_url": page.url})

            for item in raw_items[: max(1, int(max_detail_check))]:
                try:
                    html = read_detail_html(detail_page, item.detail_url)
                    info = extract_provider_from_html(html, item.detail_url)
                    provider = clean_text(info.get("provider_name", ""))
                    if not provider:
                        log.append({"step": "provider_extract", "detail_url": item.detail_url, "ok": False, "reason": "NO_PROVIDER"})
                        continue
                    key = norm_org(provider)
                    if key not in candidates:
                        candidates[key] = ProviderCandidate(
                            provider_name=provider,
                            provider_url=info.get("provider_url", ""),
                            sample_detail_url=item.detail_url,
                            sample_title=item.title or info.get("dataset_name", ""),
                            hit_count=1,
                        )
                    else:
                        candidates[key].hit_count += 1
                        if not candidates[key].provider_url and info.get("provider_url"):
                            candidates[key].provider_url = info.get("provider_url", "")
                    log.append({"step": "provider_extract", "detail_url": item.detail_url, "provider": provider, "ok": True})
                except Exception as e:
                    log.append({"step": "provider_extract", "detail_url": item.detail_url, "ok": False, "error": repr(e)})
        finally:
            context.close()
            browser.close()

    candidate_list = [asdict(c) for c in sorted(candidates.values(), key=lambda x: (-x.hit_count, x.provider_name))]
    return {
        "input_keyword": keyword,
        "list_items_checked": len(raw_items),
        "candidates": candidate_list,
        "log": log,
    }


def unique_keywords(seed_keyword: str, provider_name: str) -> List[str]:
    values = [seed_keyword, provider_name, strip_company_marker(provider_name)]
    # 한국중부발전(주) -> 한국중부발전, 중부발전도 힌트로 추가 가능
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
) -> Dict:
    """선택 기관 기준으로 상세 URL을 재수집하고, 상세페이지 제공기관 값으로 교차 검증한다."""
    provider_name = clean_text(provider_name)
    seed_keyword = clean_text(seed_keyword)
    if not provider_name:
        raise ValueError("선택 기관명이 없습니다.")

    log: List[Dict] = []
    candidate_items: Dict[str, ListItem] = {}
    verified: List[VerifiedItem] = []

    with sync_playwright() as pw:
        browser, context = setup_context(pw, headless=headless)
        list_page = context.new_page()
        detail_page = context.new_page()
        try:
            # 1) 상세페이지에서 얻은 공식 제공기관 링크가 있으면 우선 사용
            if provider_url:
                try:
                    items = collect_items_from_list_url(
                        list_page,
                        provider_url,
                        max_pages=max_pages,
                        per_page=100,
                        source_keyword="provider_official_link",
                    )
                    for item in items:
                        candidate_items[item.detail_url] = item
                    log.append({"step": "provider_url_collect", "url": provider_url, "items": len(items), "ok": True})
                except Exception as e:
                    log.append({"step": "provider_url_collect", "url": provider_url, "items": 0, "ok": False, "error": repr(e)})

            # 2) keyword 검색 기반 후보 수집. org= 사용 금지.
            for kw in unique_keywords(seed_keyword, provider_name):
                try:
                    items = collect_items_by_keyword(list_page, kw, max_pages=max_pages, per_page=100)
                    for item in items:
                        candidate_items.setdefault(item.detail_url, item)
                    log.append({"step": "keyword_collect", "keyword": kw, "items": len(items), "ok": True})
                except Exception as e:
                    log.append({"step": "keyword_collect", "keyword": kw, "items": 0, "ok": False, "error": repr(e)})

            # 3) 상세페이지 제공기관으로 최종 검증
            for idx, item in enumerate(candidate_items.values(), start=1):
                if max_items and len(verified) >= int(max_items):
                    break
                try:
                    html = read_detail_html(detail_page, item.detail_url)
                    info = extract_provider_from_html(html, item.detail_url)
                    actual_provider = clean_text(info.get("provider_name", ""))
                    matched = org_matches(provider_name, actual_provider)
                    log.append({
                        "step": "detail_cross_validate",
                        "detail_url": item.detail_url,
                        "title": item.title,
                        "expected_provider": provider_name,
                        "actual_provider": actual_provider,
                        "matched": matched,
                    })
                    if matched:
                        verified.append(
                            VerifiedItem(
                                title=item.title or info.get("dataset_name", ""),
                                detail_url=item.detail_url,
                                provider_name=actual_provider,
                                provider_url=info.get("provider_url", "") or provider_url,
                                view_count=item.view_count,
                                download_count=item.download_count,
                                source_list_url=item.source_list_url,
                                source_keyword=item.source_keyword,
                            )
                        )
                except Exception as e:
                    log.append({"step": "detail_cross_validate", "detail_url": item.detail_url, "matched": False, "error": repr(e)})
        finally:
            context.close()
            browser.close()

    resolution = {
        "selected_provider": provider_name,
        "seed_keyword": seed_keyword,
        "provider_url": provider_url,
        "resolver_method": "keyword_or_provider_link_then_detail_provider_cross_validation",
        "candidate_detail_url_count": len(candidate_items),
        "verified_detail_url_count": len(verified),
        "detail_items": [asdict(v) for v in verified],
        "log": log,
    }
    return resolution


def save_resolution(resolution: Dict, output_dir: str = "outputs/resolution") -> str:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    provider = clean_text(resolution.get("selected_provider", "기관")) or "기관"
    safe = re.sub(r"[\\/:*?\"<>|]", "_", provider)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = Path(output_dir) / f"resolution_{safe}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(resolution, f, ensure_ascii=False, indent=2)
    return str(path)
