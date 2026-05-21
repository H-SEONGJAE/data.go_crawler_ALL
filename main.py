# -*- coding: utf-8 -*-
import time
import urllib.parse
import re
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from streamlit_option_menu import option_menu

from streamlit_task_ui import (
    create_task_dir,
    download_file_button,
    python_cmd,
    render_task_panel,
    start_process_task,
)


BASE_URL = "https://www.data.go.kr"
HEADERS = {"User-Agent": "Mozilla/5.0"}

ALL_SELECTABLE_COLUMNS = [
    "최종순번", "파일데이터명", "제공기관", "분류체계", "확장자", "전체 행", "키워드", "설명",
    "컬럼목록", "상세페이지 URL", "관리부서명", "관리부서 전화번호", "보유근거", "수집방법",
    "업데이트 주기", "차기 등록 예정일", "매체유형", "데이터 한계", "조회수", "다운로드(바로가기)",
    "등록일", "수정일", "제공형태", "기타 유의사항", "공간범위", "시간범위", "비용부과유무",
    "비용부과기준 및 단위", "이용허락범위", "수집파일",
]


def get_soup(url, timeout=15):
    res = requests.get(url, headers=HEADERS, timeout=timeout)
    res.raise_for_status()
    return BeautifulSoup(res.text, "lxml")


def get_total_pages(search_org="", per_page=10):
    base_list_url = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
    encoded_org = urllib.parse.quote(search_org) if search_org else ""
    list_url = f"{base_list_url}?dType=FILE&sort=updtDt&currentPage=1&perPage={per_page}"
    if search_org:
        list_url += f"&org={encoded_org}"

    try:
        soup = get_soup(list_url)
        page_numbers = []
        pagination = soup.select_one("nav.pagination, div.pagination, .page")
        if pagination:
            for a in pagination.find_all("a"):
                text = a.get_text(strip=True)
                if text.isdigit():
                    page_numbers.append(int(text))
                onclick = a.get("onclick", "")
                for n in __import__("re").findall(r"\d+", onclick):
                    page_numbers.append(int(n))
                href = a.get("href", "")
                for n in __import__("re").findall(r"currentPage=(\d+)", href):
                    page_numbers.append(int(n))
        if page_numbers:
            return max(page_numbers)
        if soup.select("a[href*='/data/'], a[href*='/dataset/']"):
            return 1
    except Exception:
        pass
    return 0


def normalize_org_name(user_input: str) -> str:
    """기관명 입력값을 검색/URL 생성에 안전한 형태로 정리합니다.

    중요:
    - 기관명을 임의로 (주)/㈜ 변형하지 않습니다.
    - 사용자가 입력한 공식 기관명을 그대로 보존합니다.
    - 공공데이터포털 제공기관 필터는 orgFullName/orgFilter/org를 함께 넣어야 안정적입니다.
    """
    return re.sub(r"\s+", " ", str(user_input or "").strip())


def build_org_file_list_url(org_name: str, current_page: int = 1, per_page: int = 10) -> str:
    """공공데이터포털 기관별 파일데이터 목록 URL을 생성합니다.

    기존 검색 오류 원인:
    - org 파라미터 하나만 넣거나 기관명 변형을 여러 번 요청하면서
      포털 응답 지연/오탐/검색 실패가 발생했습니다.

    개선:
    - 포털 UI에서 복사되는 URL 형식에 맞춰 orgFullName, orgFilter, org를 모두 동일 기관명으로 채웁니다.
    - urlencode를 사용해 공백을 '+'로 인코딩합니다.
    """
    org = normalize_org_name(org_name)
    params = {
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
        "orgFullName": org,
        "orgFilter": org,
        "org": org,
        "orgSearch": "",
        "currentPage": str(int(current_page or 1)),
        "perPage": str(int(per_page or 10)),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)


def quick_check_org_url(org_name: str, timeout: int = 8):
    """기관 URL을 1회만 가볍게 확인합니다.

    반환값:
    - True  : 1페이지에서 파일데이터 링크가 확인됨
    - False : 응답은 받았지만 파일데이터 링크를 확인하지 못함
    - None  : 네트워크/포털 응답 지연 등으로 확인 실패

    주의:
    이 함수는 수집 가능 여부를 막기 위한 검증이 아니라, UI 안내용입니다.
    실제 수집은 metadata_runner/crawler.py 원본 엔진이 판단합니다.
    """
    url = build_org_file_list_url(org_name, current_page=1, per_page=10)
    try:
        soup = get_soup(url, timeout=timeout)
        links = soup.select("a[href*='/data/'], a[href*='/dataset/']")
        return bool(links), url
    except Exception:
        return None, url


def find_valid_org_name(user_input):
    """기존 호출부 호환용.

    더 이상 기관명을 여러 형태로 변형하면서 포털에 반복 요청하지 않습니다.
    검색 실패/지연을 막기 위해 입력 기관명 그대로 URL만 생성합니다.
    """
    org = normalize_org_name(user_input)
    ok, _ = quick_check_org_url(org)
    if ok is True:
        return org, 1
    if ok is False:
        return org, 0
    # 확인 실패는 차단 사유가 아니므로 수집 버튼은 사용할 수 있게 -1로 표시합니다.
    return org, -1


def section_title(title):
    st.markdown(
        f"""
        <div style="border-left: 5px solid #1F2937; padding-left: 15px; margin-bottom: 20px;">
            <span style="font-size: 26px; font-weight: 800; color: #1F2937;">{title}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_guide(steps):
    cards = "".join(
        f"""
        <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
            <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP {i}</div>
            <div style="font-size: 14px; color: #475569; line-height: 1.5;">{text}</div>
        </div>
        """
        for i, text in enumerate(steps, start=1)
    )
    st.markdown(
        f"""
        <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
            <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
            <div style="display: flex; gap: 15px;">{cards}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_metadata_downloads(result, prefix):
    if not result:
        return

    metadata_path = result.get("metadata_path")
    fail_path = result.get("fail_path")

    col1, col2 = st.columns(2)
    with col1:
        download_file_button(
            metadata_path,
            "📥 메타데이터.xlsx 다운로드",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{prefix}_metadata_download",
        )
    with col2:
        download_file_button(
            fail_path,
            "📥 실패로그.xlsx 다운로드",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{prefix}_fail_download",
        )

    if metadata_path and Path(metadata_path).exists():
        st.markdown("**선택 컬럼 파일이 필요한 경우에만 사용하세요. 원본 메타데이터.xlsx는 그대로 유지됩니다.**")
        selected = st.multiselect(
            "다운로드할 컬럼 선택",
            options=ALL_SELECTABLE_COLUMNS,
            default=[c for c in ["파일데이터명", "제공기관", "분류체계", "설명", "컬럼목록", "상세페이지 URL"] if c in ALL_SELECTABLE_COLUMNS],
            key=f"{prefix}_selected_cols",
        )
        if selected:
            try:
                df = pd.read_excel(metadata_path)
                filtered = df.reindex(columns=[c for c in selected if c in df.columns])
                import io
                bio = io.BytesIO()
                with pd.ExcelWriter(bio, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
                    filtered.to_excel(writer, index=False, sheet_name="메타데이터")
                bio.seek(0)
                st.download_button(
                    "📥 선택 컬럼 엑셀 다운로드",
                    data=bio,
                    file_name=f"선택컬럼_{Path(metadata_path).name}",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                    key=f"{prefix}_filtered_download",
                )
            except Exception as e:
                st.warning(f"선택 컬럼 파일 생성 실패: {e}")


def render_metadata_page():
    section_title("공공데이터 포털 메타데이터 크롤링")
    tab_all, tab_org = st.tabs(["1️⃣ 전체 데이터 수집", "2️⃣ 기관별 수집"])

    with tab_all:
        render_guide([
            "공공데이터포털 전체 파일데이터 메타데이터를 수집합니다.",
            "검증된 crawler_metadata.py 원본 엔진을 그대로 실행합니다.",
            "완료 후 메타데이터.xlsx와 실패로그.xlsx를 다운로드합니다.",
        ])
        st.warning("전체 8만 건 이상 수집은 시간이 오래 걸릴 수 있으므로 로컬 실행을 권장합니다.")

        col1, col2, col3 = st.columns(3)
        with col1:
            max_pages = st.number_input("최대 목록 페이지", min_value=0, value=100, step=10, help="0이면 빈 페이지가 나올 때까지 진행합니다.")
        with col2:
            max_items = st.number_input("최대 상세 건수", min_value=0, value=100000, step=10000, help="0이면 제한 없이 진행합니다.")
        with col3:
            run_mode = st.selectbox("실행 모드", ["BOTH", "MAIN", "RETRY_FAILED"], index=0, help="BOTH는 메인 수집 후 실패로그 재수집을 수행합니다.")

        if st.button("전체 메타데이터 수집 시작", type="primary", use_container_width=True, key="start_meta_all"):
            task_dir = create_task_dir("metadata", "all")
            result_json = task_dir / "result.json"
            cmd = python_cmd(
                "metadata_runner.py",
                "--scope", "all",
                "--run-mode", run_mode,
                "--output-dir", str(task_dir / "result"),
                "--result-json", str(result_json),
                "--max-pages", str(max_pages),
                "--max-items", str(max_items),
            )
            start_process_task("task_meta_all", cmd, task_dir)
            st.rerun()

        result = render_task_panel("task_meta_all", "전체 메타데이터 수집 진행상황")
        render_metadata_downloads(result, "all")

    with tab_org:
        render_guide([
            "제공기관명을 입력하고 검색합니다.",
            "기관 조건 URL만 생성하고, 상세 수집은 crawler_metadata.py 원본 엔진을 그대로 사용합니다.",
            "완료 후 메타데이터.xlsx와 실패로그.xlsx를 다운로드합니다.",
        ])

        st.markdown("**▪ 제공기관명 입력**")
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            org_input = st.text_input("제공기관", label_visibility="collapsed", placeholder="예: 한국중부발전(주)", key="org_meta_input")
        with col_btn:
            if st.button("검색", icon=":material/search:", use_container_width=True, key="search_org_meta"):
                org = normalize_org_name(org_input)
                if not org:
                    st.warning("제공기관명을 입력해주세요.")
                else:
                    ok, target_url = quick_check_org_url(org)
                    st.session_state["meta_org_exact"] = org
                    st.session_state["meta_org_check"] = "ok" if ok is True else ("empty" if ok is False else "unknown")
                    st.session_state["meta_org_target_url"] = target_url

        exact_org = st.session_state.get("meta_org_exact", "")
        check_state = st.session_state.get("meta_org_check", "")
        target_url_preview = st.session_state.get("meta_org_target_url", "")
        if exact_org:
            if check_state == "ok":
                st.success(f"기관 URL 생성 및 1페이지 확인 완료: {exact_org}")
            elif check_state == "empty":
                st.warning(f"기관 URL은 생성했지만 1페이지에서 파일데이터 링크를 확인하지 못했습니다. 기관명이 정확하면 수집을 실행해도 됩니다: {exact_org}")
            else:
                st.info(f"기관 URL을 생성했습니다. 포털 응답 지연으로 사전 확인은 생략되었습니다: {exact_org}")
            with st.expander("생성된 기관별 파일데이터 URL 보기", expanded=False):
                st.code(target_url_preview or build_org_file_list_url(exact_org), language="text")

        col1, col2 = st.columns(2)
        with col1:
            org_run_mode = st.selectbox("기관별 실행 모드", ["BOTH", "MAIN"], index=0, key="org_meta_run_mode")
        with col2:
            org_max_pages = st.number_input("기관별 최대 목록 페이지", min_value=0, value=0, step=10, key="org_meta_max_pages", help="0이면 빈 페이지가 나올 때까지 진행합니다.")

        if st.button("기관별 메타데이터 수집 시작", type="primary", use_container_width=True, key="start_meta_org"):
            org_to_run = exact_org or org_input.strip()
            if not org_to_run:
                st.error("제공기관명을 입력해주세요.")
            else:
                task_dir = create_task_dir("metadata", f"org_{org_to_run}")
                result_json = task_dir / "result.json"
                cmd = python_cmd(
                    "metadata_runner.py",
                    "--scope", "org",
                    "--org-name", org_to_run,
                    "--run-mode", org_run_mode,
                    "--output-dir", str(task_dir / "result"),
                    "--result-json", str(result_json),
                    "--max-pages", str(org_max_pages),
                    "--max-items", "0",
                )
                start_process_task("task_meta_org", cmd, task_dir)
                st.rerun()

        result = render_task_panel("task_meta_org", "기관별 메타데이터 수집 진행상황")
        render_metadata_downloads(result, "org")


def render_stats_page():
    section_title("기관별 데이터 조회수 및 다운로드 수")
    render_guide([
        "제공기관명을 입력하고 검색합니다.",
        "검증 완료된 crawler.py 원본 Selenium 크롤러를 그대로 실행합니다.",
        "완료 후 조회수/다운로드 수 엑셀을 다운로드합니다.",
    ])

    st.markdown("**▪ 제공기관명 입력**")
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        org_input = st.text_input("제공기관", label_visibility="collapsed", placeholder="예: 한국중부발전(주)", key="org_stats_input")
    with col_btn:
        if st.button("검색", icon=":material/search:", use_container_width=True, key="search_org_stats"):
            org = normalize_org_name(org_input)
            if not org:
                st.warning("제공기관명을 입력해주세요.")
            else:
                ok, target_url = quick_check_org_url(org)
                st.session_state["stats_org_exact"] = org
                st.session_state["stats_org_check"] = "ok" if ok is True else ("empty" if ok is False else "unknown")
                st.session_state["stats_org_target_url"] = target_url

    exact_org = st.session_state.get("stats_org_exact", "")
    check_state = st.session_state.get("stats_org_check", "")
    target_url_preview = st.session_state.get("stats_org_target_url", "")
    if exact_org:
        if check_state == "ok":
            st.success(f"기관 URL 생성 및 1페이지 확인 완료: {exact_org}")
        elif check_state == "empty":
            st.warning(f"기관 URL은 생성했지만 1페이지에서 파일데이터 링크를 확인하지 못했습니다. 기관명이 정확하면 수집을 실행해도 됩니다: {exact_org}")
        else:
            st.info(f"기관 URL을 생성했습니다. 포털 응답 지연으로 사전 확인은 생략되었습니다: {exact_org}")
        with st.expander("생성된 기관별 파일데이터 URL 보기", expanded=False):
            st.code(target_url_preview or build_org_file_list_url(exact_org), language="text")

    if st.button("조회수 및 다운로드 수 수집 시작", type="primary", use_container_width=True, key="start_stats"):
        org_to_run = exact_org or org_input.strip()
        if not org_to_run:
            st.error("제공기관명을 입력해주세요.")
        else:
            task_dir = create_task_dir("stats", org_to_run)
            result_json = task_dir / "result.json"
            cmd = python_cmd(
                "stats_runner.py",
                "--org-name", org_to_run,
                "--output-dir", str(task_dir / "result"),
                "--result-json", str(result_json),
            )
            start_process_task("task_stats", cmd, task_dir)
            st.rerun()

    result = render_task_panel("task_stats", "조회수 및 다운로드 수 수집 진행상황")
    if result:
        download_file_button(
            result.get("excel_path"),
            "📥 조회수/다운로드 수 엑셀 다운로드",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="stats_excel_download",
        )


def render_download_page():
    section_title("기관별 포털 파일데이터 다운로드 크롤러")
    render_guide([
        "기관명과 기관별 파일데이터 페이지 URL을 입력합니다.",
        "crawler_data.py의 Playwright 다운로드 로직을 직접 실행합니다.",
        "현재데이터/과거데이터 다운로드 완료 후 ZIP 파일을 다운로드합니다.",
    ])

    inst_name = st.text_input("기관명", placeholder="예: 한국중부발전(주)", key="download_inst")
    col_url, col_gen = st.columns([4, 1])
    with col_url:
        org_url = st.text_input("기관별 파일데이터 페이지 URL", placeholder="기관명을 입력한 뒤 [URL 생성]을 누르거나 공공데이터포털 URL을 직접 입력", key="download_url")
    with col_gen:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        if st.button("URL 생성", use_container_width=True, key="download_url_make"):
            org = normalize_org_name(inst_name)
            if not org:
                st.warning("기관명을 먼저 입력해주세요.")
            else:
                st.session_state["download_url"] = build_org_file_list_url(org)
                st.rerun()
    headless = st.checkbox("브라우저 숨김 실행", value=True, key="download_headless")

    if st.button("파일데이터 다운로드 시작", type="primary", use_container_width=True, key="start_download"):
        if not inst_name.strip() or not org_url.strip():
            st.error("기관명과 기관 URL을 모두 입력해주세요.")
        else:
            task_dir = create_task_dir("downloads", inst_name)
            result_json = task_dir / "result.json"
            cmd = python_cmd(
                "download_runner.py",
                "--inst-name", inst_name.strip(),
                "--org-url", org_url.strip(),
                "--output-dir", str(task_dir / "result"),
                "--result-json", str(result_json),
                "--headless", "true" if headless else "false",
            )
            start_process_task("task_download", cmd, task_dir)
            st.rerun()

    result = render_task_panel("task_download", "파일데이터 다운로드 진행상황")
    if result:
        download_file_button(
            result.get("zip_path"),
            "📥 파일데이터 ZIP 다운로드",
            "application/zip",
            key="download_zip_button",
        )


st.set_page_config(page_title="공공데이터 크롤러", page_icon="🏢", layout="wide")

st.markdown(
    """
    <style>
    div.stButton > button { height: 42px; }
    input::placeholder { font-size: 14px !important; }
    div[data-testid="stMetric"] { background-color: #F8FAFC; padding: 10px; border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### ☰ 메뉴")
    menu = option_menu(
        menu_title=None,
        options=["메타데이터 크롤링", "조회수 및 다운로드 수", "파일데이터 다운로드"],
        icons=["database", "bar-chart-line", "cloud-download"],
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "#6B7280", "font-size": "18px"},
            "nav-link": {"font-size": "14.5px", "text-align": "left", "margin": "6px 0px", "--hover-color": "#F3F4F6", "border-radius": "8px"},
            "nav-link-selected": {"background-color": "#EF4444", "color": "white", "font-weight": "bold", "border-radius": "8px"},
        },
    )

if menu == "메타데이터 크롤링":
    render_metadata_page()
elif menu == "조회수 및 다운로드 수":
    render_stats_page()
elif menu == "파일데이터 다운로드":
    render_download_page()
