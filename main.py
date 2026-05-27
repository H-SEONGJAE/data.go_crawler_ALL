# -*- coding: utf-8 -*-
"""
main.py

공공데이터포털 크롤링 통합 Streamlit 앱
- 메타데이터 크롤링
  1) 전체 데이터 수집: crawler_metadata.py 직접 호출
  2) 기관별 수집: page1_org_metadata.py 호출
- 조회수 및 다운로드 수: 기관명만 입력하면 기관별 FILE 목록 URL 자동 생성 후 수집
- 파일데이터 다운로드: crawler_data.py 함수형 엔진 호출 후 ZIP 다운로드

핵심 반영사항
1. 사진 3장과 동일한 기관별 검색 URL 구조를 build_org_search_url / build_org_file_list_url로 공통화
2. org, orgFilter, orgFullName, conditionType=search를 모든 기관별 기능에 반영
3. 기존 EXE/새 콘솔 실행 방식 제거, Streamlit 내부에서 함수 호출
4. 파일데이터 다운로드는 끝 페이지 반복 방지를 위해 currentPage 직접 증가 + 반복 시그니처 감지 방식 사용
"""

import contextlib
import io
import math
import os
import random
import re
import time
import urllib.parse
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from streamlit_option_menu import option_menu

import crawler_data
import crawler_metadata
import page1_org_metadata
from crawler import collect_file_data_by_org


# ==========================================
# 0. 전역 변수 및 설정
# ==========================================
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

TARGET_METADATA_KEYS = [
    "파일데이터명", "분류체계", "제공기관", "관리부서명", "관리부서 전화번호",
    "보유근거", "수집방법", "업데이트 주기", "차기 등록 예정일", "매체유형",
    "전체 행", "확장자", "키워드", "데이터 한계", "다운로드(바로가기)",
    "등록일", "수정일", "제공형태", "설명", "기타 유의사항",
    "공간범위", "시간범위", "비용부과유무", "비용부과기준 및 단위", "이용허락범위",
]

METADATA_KEY_MAP = {k.replace(" ", ""): k for k in TARGET_METADATA_KEYS}
METADATA_KEY_MAP["다운로드바로가기"] = "다운로드(바로가기)"
METADATA_KEY_MAP["비용부과기준및단위"] = "비용부과기준 및 단위"
METADATA_KEY_MAP["전화번호"] = "관리부서 전화번호"
METADATA_KEY_MAP["담당자전화번호"] = "관리부서 전화번호"
METADATA_KEY_MAP["연락처"] = "관리부서 전화번호"

ALL_SELECTABLE_COLUMNS = [
    "파일데이터명", "분류체계", "제공기관", "관리부서명", "관리부서 전화번호", "설명",
    "키워드", "컬럼목록", "전체 행", "확장자", "매체유형", "제공형태", "업데이트 주기",
    "차기 등록 예정일", "등록일", "수정일", "보유근거", "수집방법", "데이터 한계",
    "기타 유의사항", "공간범위", "시간범위", "비용부과유무", "비용부과기준 및 단위",
    "이용허락범위", "다운로드(바로가기)", "상세페이지 URL",
]


# ==========================================
# 1. 기관별 검색 URL 공통 생성 함수
# ==========================================

def clean_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_data_go_kr_list_url(
    org_name: str = "",
    d_type: str = "",
    current_page: int = 1,
    per_page: int = 10,
    sort: str = "updtDt",
) -> str:
    """
    사진 3장과 동일한 기관별 검색 URL 구조를 생성합니다.

    예시 핵심 파라미터:
    /tcs/dss/selectDataSetList.do?dType=&keyword=&org=한국중부발전%28주%29
    &orgFilter=한국중부발전%28주%29&orgFullName=한국중부발전%28주%29&conditionType=search

    d_type=""   : 기관 검색 결과 기본 URL. 다운로드 엔진의 사전 접속/화면 검증에 사용
    d_type="FILE": 파일데이터 목록 직접 수집에 사용
    """
    org_name = clean_text(org_name)
    params = {
        "dType": d_type,
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
        "sort": sort,
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


def build_org_search_url(org_name: str, current_page: int = 1, per_page: int = 10) -> str:
    """사진의 주소창과 같은 기관별 검색 URL입니다. dType은 비워둡니다."""
    return build_data_go_kr_list_url(org_name, d_type="", current_page=current_page, per_page=per_page)


def build_org_file_list_url(org_name: str, current_page: int = 1, per_page: int = 100) -> str:
    """실제 FILE 목록/상세 URL 수집용 URL입니다."""
    return build_data_go_kr_list_url(org_name, d_type="FILE", current_page=current_page, per_page=per_page)


# ==========================================
# 2. 공통 요청/검색 보정 함수
# ==========================================

def get_soup(url: str, max_retries: int = 3) -> BeautifulSoup:
    last_error = None
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.2, 0.5))
            res = requests.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
            return BeautifulSoup(res.text, "lxml")
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1.0, 2.0))
    raise last_error


def get_html(url: str, max_retries: int = 3) -> str:
    """목록 페이지 원본 HTML을 반환합니다. 상세 URL 수집에서는 원본 HTML 기준 파싱이 더 안정적입니다."""
    last_error = None
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.2, 0.5))
            res = requests.get(url, headers=HEADERS, timeout=25)
            res.raise_for_status()
            return res.text
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(random.uniform(1.0, 2.0))
    raise last_error


def extract_total_count(soup: BeautifulSoup) -> int:
    text = clean_text(soup.get_text(" "))
    patterns = [
        r"총\s*([0-9,]+)\s*건이\s*검색",
        r"총\s*([0-9,]+)\s*건",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return int(m.group(1).replace(",", ""))
    return 0


def get_total_count_and_pages(search_org: str, per_page: int = 10, d_type: str = "FILE") -> Tuple[int, int]:
    """
    기관 검색 결과 건수/페이지 수를 확인합니다.
    우선 총 N건 문구를 사용하고, 실패하면 페이지네이션 숫자를 fallback으로 사용합니다.
    """
    list_url = build_data_go_kr_list_url(search_org, d_type=d_type, current_page=1, per_page=per_page)
    try:
        soup = get_soup(list_url)
        total_count = extract_total_count(soup)
        if total_count > 0:
            return total_count, max(1, math.ceil(total_count / per_page))

        page_numbers = []
        pagination = soup.select_one("nav.pagination, div.pagination, .page")
        if pagination:
            for a in pagination.find_all("a"):
                num_text = re.sub(r"\D", "", a.get_text())
                if num_text:
                    page_numbers.append(int(num_text))
                onclick = a.get("onclick", "")
                nums_in_onclick = re.findall(r"\d+", onclick)
                page_numbers.extend([int(n) for n in nums_in_onclick])

        if page_numbers:
            pages = max(page_numbers)
            return pages * per_page, pages

        if soup.select("div.result-list ul li, a[href*='/data/'], a[href*='/dataset/']"):
            return per_page, 1
    except Exception:
        pass
    return 0, 0


def make_org_variations(user_input: str) -> List[str]:
    base = clean_text(user_input)
    compact = base.replace(" ", "")

    variations = [
        base,
        compact,
        base + "㈜",
        base + "(주)",
        "㈜" + base,
        "(주)" + base,
        base.replace("(주)", "㈜"),
        base.replace("㈜", "(주)"),
        compact + "㈜",
        compact + "(주)",
        base + "(재)",
        "(재)" + base,
        compact + "(재)",
    ]
    # 이상한 중복/빈값 제거
    cleaned = []
    for v in variations:
        v = clean_text(v)
        if v and v not in cleaned:
            cleaned.append(v)
    return cleaned


def find_valid_org_info(user_input: str) -> Dict[str, object]:
    """기관명 자동 교정 + FILE 기준 총건수/페이지 + 기관 검색 URL 반환."""
    for var in make_org_variations(user_input):
        total_count, total_pages = get_total_count_and_pages(var, per_page=10, d_type="FILE")
        if total_pages > 0:
            return {
                "org_name": var,
                "total_count": total_count,
                "total_pages": total_pages,
                "search_url": build_org_search_url(var, current_page=1, per_page=10),
                "file_list_url": build_org_file_list_url(var, current_page=1, per_page=100),
            }
    base = clean_text(user_input)
    return {
        "org_name": base,
        "total_count": 0,
        "total_pages": 0,
        "search_url": build_org_search_url(base, current_page=1, per_page=10),
        "file_list_url": build_org_file_list_url(base, current_page=1, per_page=100),
    }


def find_valid_org_name(user_input: str):
    """기존 page1_org_metadata.py 호환용: (기관명, 페이지수) 반환."""
    info = find_valid_org_info(user_input)
    return info["org_name"], info["total_pages"]


def format_tel_no(tel):
    tel = re.sub(r"\D", "", str(tel))
    if len(tel) == 8:
        return f"{tel[:4]}-{tel[4:]}"
    if len(tel) == 9:
        return f"{tel[:2]}-{tel[2:5]}-{tel[5:]}"
    if len(tel) == 10:
        if tel.startswith("02"):
            return f"{tel[:2]}-{tel[2:6]}-{tel[6:]}"
        return f"{tel[:3]}-{tel[3:6]}-{tel[6:]}"
    if len(tel) == 11:
        return f"{tel[:3]}-{tel[3:7]}-{tel[7:]}"
    return tel


def collect_detail_items_by_org(org_name: str, total_pages: int = 0, progress_callback=None) -> List[Dict[str, object]]:
    """
    기관별 FILE 목록에서 상세페이지 item 목록을 수집합니다.

    기존에는 a[href]만 단순 수집해 일부 목록의 제목/URL 매칭과 오류 원인 파악이 어려웠습니다.
    여기서는 crawler_metadata.py의 목록 카드 파서와 동일한 방식으로 title, 확장자, 조회수,
    다운로드수, detail_url을 함께 수집합니다.

    종료 조건:
    1) 목록이 비어 있으면 종료
    2) 끝 페이지 이후 같은 URL 목록이 반복되면 종료
    3) 신규 상세 URL이 없으면 종료
    """
    items: List[Dict[str, object]] = []
    seen_urls = set()
    seen_signatures = set()

    per_page = 100
    max_fast_pages = max(1, math.ceil((int(total_pages or 1) * 10) / per_page))

    for page_no in range(1, max_fast_pages + 6):
        list_url = build_org_file_list_url(org_name, current_page=page_no, per_page=per_page)
        if progress_callback:
            progress_callback(page_no, max_fast_pages, f"상세 URL 수집 중... ({page_no}/{max_fast_pages})")

        try:
            html = get_html(list_url)
        except Exception:
            break

        try:
            page_items = crawler_metadata.collect_dataset_links_from_html(html, list_url)
        except Exception:
            # crawler_metadata 파서가 실패할 경우 기본 a 태그 방식으로 비상 수집
            soup = BeautifulSoup(html, "lxml")
            page_items = []
            for a in soup.select("a[href*='/data/'], a[href*='/dataset/']"):
                href = a.get("href", "")
                if "fileData.do" in href or re.search(r"/(?:data|dataset)/\d+", href):
                    full_url = urllib.parse.urljoin(BASE_URL, href)
                    title = clean_text(a.get_text(" "))
                    page_items.append({
                        "raw_title": title,
                        "title": title,
                        "title_source": "fallback_anchor",
                        "확장자": "",
                        "조회수": "",
                        "다운로드(바로가기)": "",
                        "다운로드수": "",
                        "detail_url": full_url,
                        "source_list_url": list_url,
                    })

        page_urls = [clean_text(item.get("detail_url", "")) for item in page_items if clean_text(item.get("detail_url", ""))]
        # URL 기준 중복 제거
        deduped_page_items = []
        local_seen = set()
        for item in page_items:
            url = clean_text(item.get("detail_url", ""))
            if not url or url in local_seen:
                continue
            local_seen.add(url)
            deduped_page_items.append(item)

        signature = tuple(clean_text(item.get("detail_url", "")) for item in deduped_page_items)
        if signature and signature in seen_signatures:
            break
        if signature:
            seen_signatures.add(signature)

        if not deduped_page_items:
            break

        new_count = 0
        for item in deduped_page_items:
            url = clean_text(item.get("detail_url", ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            items.append(item)
            new_count += 1

        if new_count == 0:
            break

    return items


def collect_detail_urls_by_org(org_name: str, total_pages: int = 0, progress_callback=None) -> List[str]:
    """기존 page1_org_metadata.py 호환용: 상세 URL만 반환합니다."""
    items = collect_detail_items_by_org(org_name, total_pages=total_pages, progress_callback=progress_callback)
    return [clean_text(item.get("detail_url", "")) for item in items if clean_text(item.get("detail_url", ""))]

def make_excel_bytes(df: pd.DataFrame, sheet_name: str) -> BytesIO:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    output.seek(0)
    return output


# ==========================================
# 3. Streamlit UI
# ==========================================
st.set_page_config(page_title="공공데이터 포털 Crawler", page_icon="🌟", layout="wide")

st.markdown(
    """
    <style>
    .title-spacer { margin-bottom: 35px; }
    div.stButton > button { height: 42px; }
    div.stButton > button p {
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
        gap: 6px !important; margin: 0 !important;
    }
    input::placeholder { font-size: 14px !important; }
    div[data-baseweb="select"] * { font-size: 14px !important; }
    div[data-baseweb="input"] {
        border: 2px solid transparent !important; height: 42px !important;
        background-color: #F3F3F3 !important; border-radius: 10px !important;
        transition: all 0.5s !important;
    }
    div[data-baseweb="input"]:hover, div[data-baseweb="input"]:focus-within {
        border: 2px solid #4A9DEC !important;
        box-shadow: 0px 0px 0px 7px rgba(74, 157, 236, 0.2) !important;
        background-color: white !important;
    }
    div[data-baseweb="input"] input { background-color: transparent !important; }
    div[data-testid="InputInstructions"] { display: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown(
        """
        <style>
        .menu-container { display: flex; align-items: center; gap: 12px; margin-top: 10px; margin-bottom: 25px; padding-left: 8px; }
        .hamburger-icon { font-size: 1.5rem; color: #FFFFFF; line-height: 1; }
        .menu-text { font-size: 1.25rem; font-weight: 600; color: #FFFFFF; }
        </style>
        <div class="menu-container">
            <div class="hamburger-icon">☰</div>
            <div class="menu-text">메뉴</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    menu = option_menu(
        menu_title=None,
        options=["메타데이터 Crawler", "조회수 및 다운로드 수 Crawler", "파일데이터 다운로드"],
        icons=["database", "bar-chart-line", "cloud-download"],
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "#FFFFFF", "font-size": "18px"},
            "nav-link": {"font-size": "14.5px", "text-align": "left", "margin": "6px 0px", "--hover-color": "#F3F4F6", "border-radius": "8px", "color": "#FFFFFF"},
            "nav-link-selected": {"background-color": "#EF4444", "color": "white", "font-weight": "bold", "border-radius": "8px"},
        },
    )


# ==========================================
# 메뉴 1. 메타데이터 크롤링
# ==========================================
if menu == "메타데이터 Crawler":
    st.markdown(
        """
        <div style="border-left: 5px solid #0EA5E9; padding-left: 15px; margin-bottom: 20px;">
            <span style="font-size: 26px; font-weight: 800; color: #0EA5E9;">🌟 공공데이터 포털 메타데이터 Crawler</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown("<div class='title-spacer'></div>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["1️⃣ 전체 데이터 수집", "2️⃣ 기관별 수집"])

    with tab1:
        guide_html_tab1 = """
        <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
            <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
            <div style="display: flex; gap: 15px;">
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">[전체 수집 실행] 버튼을 누르면 전체 메타데이터 수집을 실행합니다.</div>
                </div>
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">전체 목록 URL은 내부 설정값을 그대로 사용합니다.</div>
                </div>
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">수집 완료 후 메타데이터와 실패로그를 다운로드합니다.</div>
                </div>
            </div>
        </div>
        """
        st.markdown(guide_html_tab1, unsafe_allow_html=True)

        st.warning("전체 수집은 데이터 양이 많아 오래 걸릴 수 있습니다. 실행 시간에 주의하세요.")

        if st.button("전체 수집 실행", type="primary", use_container_width=True, key="run_all_metadata"):
            log_box = st.empty()
            with st.spinner("전체 메타데이터 수집 중입니다..."):
                log_buffer = io.StringIO()
                try:
                    with contextlib.redirect_stdout(log_buffer):
                        crawler_data.ensure_playwright_browser_installed()
                        crawler_metadata.main()
                    log_box.text_area("실행 로그", log_buffer.getvalue()[-10000:], height=300)

                    output_dir = crawler_metadata.resolve_main_output_dir()
                    metadata_path = Path(output_dir) / "메타데이터.xlsx"
                    fail_path = Path(output_dir) / "실패로그.xlsx"

                    st.success("✅ 전체 메타데이터 수집이 완료되었습니다.")
                    if metadata_path.exists():
                        with open(metadata_path, "rb") as f:
                            st.download_button(
                                "🌟 메타데이터.xlsx 다운로드",
                                data=f.read(),
                                file_name="메타데이터.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                    if fail_path.exists():
                        with open(fail_path, "rb") as f:
                            st.download_button(
                                "🌟 실패로그.xlsx 다운로드",
                                data=f.read(),
                                file_name="실패로그.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            )
                except Exception as e:
                    log_box.text_area("실행 로그", log_buffer.getvalue()[-10000:], height=300)
                    st.error(f"🚨 전체 수집 중 오류가 발생했습니다: {e}")

    with tab2:
        page1_org_metadata.render_tab2(
            get_soup,
            find_valid_org_name,
            format_tel_no,
            BASE_URL,
            HEADERS,
            ALL_SELECTABLE_COLUMNS,
            TARGET_METADATA_KEYS,
            METADATA_KEY_MAP,
            collect_detail_urls_by_org=collect_detail_urls_by_org,
            collect_detail_items_by_org=collect_detail_items_by_org,
            build_org_file_list_url=build_org_file_list_url,
        )


# ==========================================
# 메뉴 2. 조회수 및 다운로드 수
# ==========================================
elif menu == "조회수 및 다운로드 수 Crawler":
    st.markdown(
        """
        <div style="border-left: 5px solid #0EA5E9; padding-left: 15px; margin-bottom: 20px;">
            <span style="font-size: 26px; font-weight: 800; color: #0EA5E9;">기관별 데이터 조회수 및 다운로드 수</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    guide_html_menu2 = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">제공기관명을 입력하면 기관 검색 URL을 생성합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">FILE 목록을 수집합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">수집 완료 후 엑셀 파일을 다운로드합니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html_menu2, unsafe_allow_html=True)

    if "org_info2" not in st.session_state:
        st.session_state.org_info2 = None

    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주), (재)한국저작권보호원)")
    col1, col2 = st.columns([4, 1])

    with col1:
        org_input = st.text_input(
            "제공기관",
            label_visibility="collapsed",
            placeholder="기관명만 입력하면 해당 기관의 조회수/다운로드 수를 수집합니다.",
            key="org_input2",
        )

    with col2:
        search_clicked = st.button("검색", icon=":material/search:", use_container_width=True, key="search_btn2")

    if search_clicked:
        if not org_input.strip():
            st.warning("제공기관명을 입력해주세요!")
        else:
            with st.spinner(f"'{org_input}' 기관 검색 URL을 생성하고 결과를 확인 중입니다..."):
                info = find_valid_org_info(org_input)
                st.session_state.org_info2 = info

            if info["total_pages"] == 0:
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인하고, 2~3번 재시도 해주세요.")
            else:
                if info["org_name"] != org_input.strip():
                    st.info(f"💡 '{info['org_name']}'(으)로 자동 변환하여 검색했습니다.")
                st.success(f"✅ 검색 완료! 총 {info['total_count']:,}건 / {info['total_pages']}페이지의 파일데이터가 발견되었습니다.")
                st.caption(f"기관 검색 URL: {info['search_url']}")

    info = st.session_state.get("org_info2")
    if info and info.get("total_pages", 0) > 0:
        st.markdown("---")
        col_info, col_extract = st.columns([4, 1], vertical_alignment="center")
        with col_info:
            st.success(f"수집 대상 기관: {info['org_name']} / 예상 파일데이터 {info['total_count']:,}건")
        with col_extract:
            run_clicked = st.button("추출", type="primary", use_container_width=True, key="extract_btn2")

        status_box = st.empty()

        if run_clicked:
            org = info["org_name"]

            def update_status(msg):
                status_box.info(msg)

            with st.spinner(f"'{org}' 조회수/다운로드수 수집 중..."):
                try:
                    df = collect_file_data_by_org(
                        org,
                        status_callback=update_status,
                        list_url_builder=build_org_file_list_url,
                        per_page=100,
                    )

                    status_box.empty()
                    if df.empty:
                        st.warning("수집된 데이터가 없습니다.")
                    else:
                        st.success(f"🌟 수집 완료! 총 {len(df)}건")
                        st.dataframe(df, use_container_width=True)

                        output = make_excel_bytes(df, "FILE_집계")
                        safe_org_name = org.replace("(", "_").replace(")", "")
                        st.download_button(
                            label="🌟 엑셀(Excel) 파일 다운로드",
                            data=output,
                            file_name=f"공공데이터_{safe_org_name}_조회수_다운로드수_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                except Exception as e:
                    status_box.empty()
                    st.error(f"🚨 크롤링 중 오류가 발생했습니다: {e}")


# ==========================================
# 메뉴 3. 파일데이터 다운로드
# ==========================================
elif menu == "파일데이터 다운로드":
    st.markdown(
        """
        <div style="border-left: 5px solid #0EA5E9; padding-left: 15px; margin-bottom: 20px;">
            <span style="font-size: 26px; font-weight: 800; color: #0EA5E9;">기관별 파일데이터 다운로드</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.divider()

    guide_html_menu3 = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px;">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">제공기관명을 입력하고 [검색]을 누릅니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px;">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">최신 데이터만 받을지 과거데이터까지 받을지 선택합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px;">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">다운로드 완료 후 ZIP 파일을 내려받습니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html_menu3, unsafe_allow_html=True)

    if "org_info3" not in st.session_state:
        st.session_state.org_info3 = None

    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주), (재)한국저작권보호원)")
    col1, col2 = st.columns([4, 1])

    with col1:
        org_input3 = st.text_input(
            "제공기관(다운로드)",
            label_visibility="collapsed",
            placeholder="기관명만 입력하면 해당 기관의 파일데이터를 다운로드합니다.",
            key="org_input3",
        )

    with col2:
        search_clicked3 = st.button("검색", icon=":material/search:", use_container_width=True, key="search_btn3")

    if search_clicked3:
        if not org_input3.strip():
            st.warning("제공기관명을 입력해주세요!")
        else:
            with st.spinner(f"'{org_input3}' 기관 검색 URL을 생성하고 결과를 확인 중입니다..."):
                info3 = find_valid_org_info(org_input3)
                st.session_state.org_info3 = info3

            if info3["total_pages"] == 0:
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인하고, 2~3번 재시도 해주세요.")
            else:
                if info3["org_name"] != org_input3.strip():
                    st.info(f"💡 '{info3['org_name']}'(으)로 자동 변환하여 검색했습니다.")
                st.success(f"✅ 검색 완료! 총 {info3['total_count']:,}건 / {info3['total_pages']}페이지의 파일데이터가 발견되었습니다.")
                st.caption(f"기관 검색 URL: {info3['search_url']}")

    info3 = st.session_state.get("org_info3")
    if info3 and info3.get("total_pages", 0) > 0:
        st.markdown("---")
        include_past = st.checkbox("과거데이터까지 다운로드", value=True)
        headless = st.checkbox("브라우저 숨김 모드(headless)", value=True)

        col_info, col_run = st.columns([4, 1], vertical_alignment="center")
        with col_info:
            st.success(f"다운로드 대상 기관: {info3['org_name']} / 예상 파일데이터 {info3['total_count']:,}건")
        with col_run:
            run_download = st.button("다운로드", type="primary", use_container_width=True, key="run_download3")

        status_box = st.empty()

        if run_download:
            org = info3["org_name"]
            org_url = info3["search_url"]

            def update_status(msg):
                status_box.info(msg)

            with st.spinner(f"'{org}' 파일데이터 다운로드 중..."):
                try:
                    zip_path = crawler_data.collect_portal_files(
                        inst_name=org,
                        org_url=org_url,
                        list_url_builder=build_org_file_list_url,
                        include_past=include_past,
                        output_root=".",
                        status_callback=update_status,
                        per_page=100,
                        headless=headless,
                    )

                    status_box.empty()
                    if not zip_path or not os.path.exists(zip_path):
                        st.error("ZIP 파일 생성에 실패했습니다.")
                    else:
                        st.success("🌟 파일데이터 다운로드 및 ZIP 생성이 완료되었습니다.")
                        with open(zip_path, "rb") as f:
                            st.download_button(
                                label="🌟 ZIP 파일 다운로드",
                                data=f.read(),
                                file_name=os.path.basename(zip_path),
                                mime="application/zip",
                            )
                except Exception as e:
                    status_box.empty()
                    st.error(f"🚨 파일 다운로드 중 오류가 발생했습니다: {e}")