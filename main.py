# -*- coding: utf-8 -*-
"""
공공데이터 포털 크롤링 통합 Streamlit 메인 파일.

핵심 원칙
- 검증 완료된 크롤러의 수집/파싱 로직은 건드리지 않는다.
- Streamlit은 입력값 생성, 프로세스 실행, 진행률 표시, 결과 다운로드만 담당한다.
"""
import io
import re
import time
import urllib.parse
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

ALL_SELECTABLE_COLUMNS = [
    "최종순번", "파일데이터명", "제공기관", "분류체계", "확장자", "전체 행", "키워드", "설명",
    "컬럼목록", "상세페이지 URL", "관리부서명", "관리부서 전화번호", "보유근거", "수집방법",
    "업데이트 주기", "차기 등록 예정일", "매체유형", "데이터 한계", "조회수", "다운로드(바로가기)",
    "등록일", "수정일", "제공형태", "기타 유의사항", "공간범위", "시간범위", "비용부과유무",
    "비용부과기준 및 단위", "이용허락범위", "수집파일",
]


def make_org_candidates(user_input: str) -> list[str]:
    """기관명 검색/실행 후보를 최소한으로 생성한다. 과도한 포털 요청을 막기 위한 가벼운 후보 목록."""
    base = (user_input or "").strip()
    if not base:
        return []

    candidates = [base]

    # 발전사/공기업 등 (주) 누락 입력 대응. 이미 (주)/㈜가 있으면 중복 생성하지 않는다.
    if "(주)" not in base and "㈜" not in base:
        candidates.extend([base + "(주)", base + "㈜"])
    else:
        candidates.extend([base.replace("(주)", "㈜"), base.replace("㈜", "(주)")])

    # 행정구역 명칭 변경 대응. 목록 검색에서는 포털이 함께 보여주는 경우가 있으므로 후보만 제공한다.
    if "강원특별자치도" in base:
        candidates.append(base.replace("강원특별자치도", "강원도"))
    if "강원도" in base:
        candidates.append(base.replace("강원도", "강원특별자치도"))

    # 중복 제거
    return list(dict.fromkeys([c for c in candidates if c.strip()]))


def build_org_filter_url(org_name: str, *, current_page: int = 1, per_page: int = 1000) -> str:
    """
    기관별 파일데이터 목록 URL을 생성한다.

    중요:
    - perPage는 페이지 수가 아니라 한 페이지당 표시 건수다.
    - 누락 방지를 위해 기본값을 1000으로 둔다.
    - 실제 반복 수집은 runner에서 currentPage=1,2,3...으로 직접 증가시킨다.
    """
    org = (org_name or "").strip()
    params = {
        "dType": "FILE",
        "sort": "updtDt",
        "currentPage": str(current_page),
        "perPage": str(per_page),
        "org": org,
    }
    return "https://www.data.go.kr/tcs/dss/selectDataSetList.do?" + urllib.parse.urlencode(params)


def find_valid_org_name_fast(user_input: str) -> tuple[str, int, str]:
    """
    검색 버튼에서는 포털 요청을 보내지 않고 URL을 즉시 생성한다.

    첫 검색에서 포털 DOM이 늦게 내려와 검색 실패처럼 보이는 문제를 막기 위해
    검색 단계와 수집 단계를 분리한다. 실제 존재 여부와 수집 건수는 runner가 판단한다.
    """
    base = (user_input or "").strip()
    return base, 1 if base else 0, build_org_filter_url(base, current_page=1, per_page=1000)


def section_title(title: str):
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
        download_file_button(metadata_path, "📥 메타데이터.xlsx 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"{prefix}_metadata_download")
    with col2:
        download_file_button(fail_path, "📥 실패로그.xlsx 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"{prefix}_fail_download")

    if metadata_path and Path(metadata_path).exists():
        with st.expander("선택 컬럼 파일 생성", expanded=False):
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
            "검증된 crawler_metadata.py 엔진을 그대로 실행합니다.",
            "완료 후 메타데이터.xlsx와 실패로그.xlsx를 다운로드합니다.",
        ])
        st.warning("전체 8만 건 이상 수집은 Streamlit Cloud보다 로컬 실행을 권장합니다.")

        col1, col2, col3 = st.columns(3)
        with col1:
            max_pages = st.number_input("최대 목록 페이지", min_value=0, value=100, step=10, help="0이면 빈 페이지가 나올 때까지 진행합니다.")
        with col2:
            max_items = st.number_input("최대 상세 건수", min_value=0, value=100000, step=10000, help="0이면 제한 없이 진행합니다.")
        with col3:
            run_mode = st.selectbox("실행 모드", ["MAIN", "BOTH", "RETRY_FAILED"], index=0, help="테스트는 MAIN 권장, 실패로그 재수집까지 하려면 BOTH")

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
            "검색은 포털 요청 없이 기관별 수집 URL을 즉시 생성합니다.",
            "완료 후 메타데이터.xlsx와 실패로그.xlsx를 다운로드합니다.",
        ])

        st.markdown("**▪ 제공기관명 입력**")
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            org_input = st.text_input("제공기관", label_visibility="collapsed", placeholder="예: 한국중부발전(주), 강원특별자치도 고성군", key="org_meta_input")
        with col_btn:
            if st.button("검색", icon=":material/search:", use_container_width=True, key="search_org_meta"):
                if not org_input.strip():
                    st.warning("제공기관명을 입력해주세요.")
                else:
                    with st.spinner("기관별 수집 URL을 생성 중입니다..."):
                        exact_org, total_pages, org_url = find_valid_org_name_fast(org_input)
                    st.session_state["meta_org_exact"] = exact_org
                    st.session_state["meta_org_pages"] = total_pages
                    st.session_state["meta_org_url"] = org_url

        exact_org = st.session_state.get("meta_org_exact", "")
        total_pages = st.session_state.get("meta_org_pages", 0)
        org_url = st.session_state.get("meta_org_url", "")
        if exact_org:
            if total_pages > 0:
                st.success(f"기관 확인 완료: {exact_org}")
            else:
                st.warning(f"URL은 생성했지만 기관명은 수집 단계에서 최종 확인됩니다: {exact_org}")
            with st.expander("생성된 기관별 파일데이터 URL 보기", expanded=False):
                st.code(org_url, language="text")

        col1, col2 = st.columns(2)
        with col1:
            org_run_mode = st.selectbox("기관별 실행 모드", ["MAIN", "BOTH"], index=0, key="org_meta_run_mode", help="먼저 MAIN으로 확인 후 필요 시 BOTH 사용")
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
        "메타데이터 목록 수집 파서를 활용해 조회수/다운로드 수를 수집합니다.",
        "완료 후 조회수/다운로드 수 엑셀을 다운로드합니다.",
    ])

    st.markdown("**▪ 제공기관명 입력**")
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        org_input = st.text_input("제공기관", label_visibility="collapsed", placeholder="예: 한국중부발전(주)", key="org_stats_input")
    with col_btn:
        if st.button("검색", icon=":material/search:", use_container_width=True, key="search_org_stats"):
            if not org_input.strip():
                st.warning("제공기관명을 입력해주세요.")
            else:
                with st.spinner("기관별 수집 URL을 생성 중입니다..."):
                    exact_org, total_pages, org_url = find_valid_org_name_fast(org_input)
                st.session_state["stats_org_exact"] = exact_org
                st.session_state["stats_org_pages"] = total_pages
                st.session_state["stats_org_url"] = org_url

    exact_org = st.session_state.get("stats_org_exact", "")
    total_pages = st.session_state.get("stats_org_pages", 0)
    org_url = st.session_state.get("stats_org_url", "")
    if exact_org:
        if total_pages > 0:
            st.success(f"기관 확인 완료: {exact_org}")
        else:
            st.warning(f"URL은 생성했지만 기관명은 수집 단계에서 최종 확인됩니다: {exact_org}")
        with st.expander("생성된 기관별 파일데이터 URL 보기", expanded=False):
            st.code(org_url, language="text")

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
        download_file_button(result.get("excel_path"), "📥 조회수/다운로드 수 엑셀 다운로드", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="stats_excel_download")


def render_download_page():
    section_title("기관별 포털 파일데이터 다운로드 크롤러")
    render_guide([
        "기관명과 기관별 파일데이터 페이지 URL을 입력합니다.",
        "crawler_data.py의 Playwright 다운로드 로직을 직접 실행합니다.",
        "현재데이터/과거데이터 다운로드 완료 후 ZIP 파일을 다운로드합니다.",
    ])

    inst_name = st.text_input("기관명", placeholder="예: 한국중부발전(주)", key="download_inst")
    org_url = st.text_input("기관별 파일데이터 페이지 URL", placeholder="공공데이터포털 기관별 파일데이터 페이지 URL", key="download_url")
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
        download_file_button(result.get("zip_path"), "📥 파일데이터 ZIP 다운로드", "application/zip", key="download_zip_button")


st.set_page_config(page_title="공공데이터 크롤러", page_icon="🏢", layout="wide")

st.markdown(
    """
    <style>
    div.stButton > button { height: 42px; }
    input::placeholder { font-size: 14px !important; }
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
