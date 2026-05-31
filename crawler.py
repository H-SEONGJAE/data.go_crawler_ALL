# -*- coding: utf-8 -*-
"""
crawler.py

기관명만 입력하면 공공데이터포털의 기관별 파일데이터 목록 URL을 생성하고,
각 목록 카드의 데이터명 / 조회수 / 다운로드수를 수집합니다.

기존 Selenium 방식은 유지보수용 collect_file_data_from_url()로 남겨두고,
Streamlit에서는 collect_file_data_by_org()를 우선 사용합니다.
"""

import math
import re
import time
import random
import urllib.parse
from typing import Callable, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.data.go.kr"
BASE_LIST_URL = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


# ==========================================================
# 1. 공통 유틸
# ==========================================================

def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_dataset_title(raw_title: str) -> str:
    s = clean_text(raw_title)
    if not s:
        return ""

    file_type_pattern = (
        r"CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|"
        r"MP4|AVI|MOV|WMV|JPG|JPEG|PNG|GIF|DOCX|DOC|PPTX|PPT|"
        r"파일데이터|오픈API|API"
    )
    s = re.sub(
        rf"^((?:{file_type_pattern})\s*(?:\+|,|/|\\|｜|·|ㆍ|-)?\s*)+",
        "",
        s,
        flags=re.IGNORECASE,
    )

    status_pattern = r"New|Update|Updated|업데이트|NEW|UPDATE"
    s = re.sub(rf"^\s*(?:{status_pattern})\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(rf"\s+(?:{status_pattern})\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bNew\b", "", s, flags=re.IGNORECASE)

    cut_markers = [
        "제공기관", "분류체계", "수정일", "등록일",
        "조회수", "조회 수", "다운로드수", "다운로드 수", "다운로드", "키워드",
    ]
    cut_positions = [s.find(marker) for marker in cut_markers if s.find(marker) > 0]
    if cut_positions:
        s = s[:min(cut_positions)]

    s = re.sub(r"\s*[|｜-]\s*공공데이터포털\s*$", "", s)
    return clean_text(s)


def only_int(value) -> int:
    s = clean_text(value)
    m = re.search(r"[0-9][0-9,]*", s)
    if not m:
        return 0
    return int(m.group(0).replace(",", ""))


def build_org_file_list_url_default(org_name: str, current_page: int = 1, per_page: int = 100) -> str:
    """
    사진 3장과 같은 기관별 검색 URL 구조를 기준으로 FILE 목록 URL을 생성합니다.
    - org / orgFilter / orgFullName에 같은 기관명을 넣습니다.
    - conditionType=search를 포함합니다.
    - 실제 수집은 파일데이터만 필요하므로 dType=FILE을 사용합니다.
    """
    params = {
        "dType": "FILE",
        "keyword": "",
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
        "orgFullName": org_name,
        "orgFilter": org_name,
        "org": org_name,
        "orgSearch": "",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "conditionType": "search",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return BASE_LIST_URL + "?" + urllib.parse.urlencode(params, doseq=True)


def get_soup(url: str, max_retries: int = 3) -> BeautifulSoup:
    last_error = None
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.15, 0.35))
            res = requests.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
            return BeautifulSoup(res.text, "lxml")
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1.0, 2.0))
    raise last_error


# ==========================================================
# 2. 목록 카드 파싱
# ==========================================================

def extract_list_view_download_counts(text: str):
    text = clean_text(text)
    if not text:
        return 0, 0

    pair_patterns = [
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드수?\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*수\s*[:：]?\s*([0-9][0-9,]*)",
    ]
    for pat in pair_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))

    view = 0
    download = 0
    m1 = re.search(r"조회\s*수?\s*[:：]?\s*([0-9][0-9,]*)", text)
    m2 = re.search(r"다운로드\s*수?\s*[:：]?\s*([0-9][0-9,]*)", text)
    if m1:
        view = int(m1.group(1).replace(",", ""))
    if m2:
        download = int(m2.group(1).replace(",", ""))
    return view, download


def parse_list_items_from_soup(soup: BeautifulSoup, page_url: str):
    rows = []

    # 공공데이터포털 목록 카드 기준
    items = soup.select("div.result-list ul li")
    if not items:
        items = soup.select("#fileDataList ul li")

    for li in items:
        a = li.select_one("a[href*='/data/'], a[href*='/dataset/']")
        if not a:
            continue

        href = a.get("href", "")
        if not href:
            continue
        detail_url = urllib.parse.urljoin(BASE_URL, href)
        if "fileData.do" not in detail_url and not re.search(r"/(?:data|dataset)/\d+", detail_url):
            continue

        title_el = li.select_one("span.title") or li.select_one(".title")
        raw_title = title_el.get_text(" ") if title_el else a.get_text(" ")
        title = clean_dataset_title(raw_title)
        if not title:
            continue

        text = li.get_text(" ")
        view, download = extract_list_view_download_counts(text)
        rows.append({
            "데이터명": title,
            "조회수": view,
            "다운로드수": download,
            "상세페이지 URL": detail_url,
            "목록페이지 URL": page_url,
        })

    return rows


# ==========================================================
# 3. 기관명 기반 수집 엔진
# ==========================================================

def collect_file_data_by_org(
    org_name: str,
    status_callback: Optional[Callable[[str], None]] = None,
    list_url_builder: Optional[Callable[[str, int, int], str]] = None,
    per_page: int = 100,
    max_pages: int = 1000,
) -> pd.DataFrame:
    """
    Streamlit용 주 함수.
    기관명만 받아서 사진 3장과 동일한 기관별 검색 URL 구조로 목록 URL을 만든 뒤,
    끝 페이지까지 조회수/다운로드수를 수집합니다.
    """
    builder = list_url_builder or build_org_file_list_url_default

    def update(msg: str):
        if status_callback:
            status_callback(msg)

    results = []
    seen_urls = set()
    seen_page_signatures = set()
    no_new_rounds = 0

    for page_no in range(1, max_pages + 1):
        list_url = builder(org_name, page_no, per_page)
        update(f"📄 목록 {page_no}페이지 수집 중...")

        try:
            soup = get_soup(list_url)
        except Exception as e:
            update(f"⚠️ {page_no}페이지 요청 실패: {e}")
            break

        rows = parse_list_items_from_soup(soup, list_url)
        signature = tuple(r["상세페이지 URL"] for r in rows)

        # 범위를 넘어간 currentPage가 같은 목록을 반복 반환하는 경우 종료
        if signature and signature in seen_page_signatures:
            update("📌 이전과 동일한 목록 페이지가 반복되어 종료합니다.")
            break
        if signature:
            seen_page_signatures.add(signature)

        if not rows:
            update("📌 더 이상 수집할 목록이 없어 종료합니다.")
            break

        new_count = 0
        for row in rows:
            key = row["상세페이지 URL"]
            if key in seen_urls:
                continue
            seen_urls.add(key)
            results.append(row)
            new_count += 1

        update(f"✅ {page_no}페이지 완료: 신규 {new_count}건 / 누적 {len(results)}건")

        if new_count == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0

        if no_new_rounds >= 1:
            update("📌 신규 URL이 없어 끝 페이지로 판단하고 종료합니다.")
            break

    df = pd.DataFrame(results)
    if not df.empty:
        df = df[["데이터명", "조회수", "다운로드수", "상세페이지 URL"]]
    update(f"✅ 수집 완료: 총 {len(df)}건")
    return df


# ==========================================================
# 4. 기존 호출명 호환용
# ==========================================================

def collect_file_data_from_url(url: str, status_callback=None) -> pd.DataFrame:
    """
    기존 main.py 호환용 함수입니다.
    URL에서 org 파라미터를 찾아 collect_file_data_by_org()로 위임합니다.
    """
    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    org = query.get("org") or query.get("orgFilter") or query.get("orgFullName")
    if not org:
        raise ValueError("URL에서 org/orgFilter/orgFullName 파라미터를 찾을 수 없습니다.")
    return collect_file_data_by_org(org, status_callback=status_callback)
