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

from org_url_resolver import (
    build_org_filter_url,
    resolve_org_name_and_url_fast,
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



@st.cache_data(ttl=600, show_spinner=False)
def resolve_org_for_ui(user_input: str) -> dict:
    """
    제공기관명 검색 공통 함수.
    - (주), (재), (BAC) 등 괄호값을 임의 생성하지 않는다.
    - 포털 목록에서 실제 제공기관명을 추출한 뒤 최종 URL을 만든다.
    - 같은 입력값은 10분간 캐시하여 검색 속도를 높인다.
    """
    return resolve_org_name_and_url_fast(
        user_input,
        headers=HEADERS,
        timeout=3,
        per_page=10,
        max_workers=4,
    )


def search_org_to_state(user_input: str, prefix: str):
    """검색 결과를 Streamlit session_state에 저장한다."""
    result = resolve_org_for_ui(user_input.strip())
    exact_org = result.get("exact_org", user_input.strip())
    org_url = result.get("url", "") or build_org_filter_url(exact_org, current_page=1, per_page=10)
    candidates = result.get("candidates", []) or []

    st.session_state[f"{prefix}_org_exact"] = exact_org
    st.session_state[f"{prefix}_org_pages"] = 1 if result.get("found") else 0
    st.session_state[f"{prefix}_org_url"] = org_url
    st.session_state[f"{prefix}_org_candidates"] = candidates
    st.session_state[f"{prefix}_org_resolve_result"] = result
    return result


def render_org_resolution(prefix: str, input_value: str):
    """검색 결과 표시 및 후보가 여러 개인 경우 선택 UI를 제공한다."""
    exact_org = st.session_state.get(f"{prefix}_org_exact", "")
    total_pages = st.session_state.get(f"{prefix}_org_pages", 0)
    org_url = st.session_state.get(f"{prefix}_org_url", "")
    candidates = st.session_state.get(f"{prefix}_org_candidates", []) or []

    if candidates and len(candidates) > 1:
        selected = st.selectbox(
            "제공기관 후보가 여러 개입니다. 실제 수집할 기관명을 선택하세요.",
            options=candidates,
            index=candidates.index(exact_org) if exact_org in candidates else 0,
            key=f"{prefix}_org_candidate_select",
        )
        if selected and selected != exact_org:
            exact_org = selected
            org_url = build_org_filter_url(selected, current_page=1, per_page=10)
            st.session_state[f"{prefix}_org_exact"] = exact_org
            st.session_state[f"{prefix}_org_url"] = org_url
            st.session_state[f"{prefix}_org_pages"] = 1
            total_pages = 1

    if exact_org:
        if total_pages > 0:
            st.success(f"기관 확인 완료: {exact_org}")
            st.caption("수집 시작 시 실제 파일데이터 목록 URL을 한 번 더 확인한 뒤 크롤링을 실행합니다.")
        else:
            st.warning(
                "빠른 검색 확인에서는 정확한 제공기관명을 확정하지 못했습니다. "
                "수집 시작 시 입력 기관명 기준으로 실제 파일데이터 목록을 다시 탐지합니다."
            )

    return exact_org, total_pages, org_url

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
            "검색은 제공기관명 확인용이며, 실제 수집 시 URL을 다시 검증합니다.",
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
                    with st.spinner("기관명 확인 중입니다..."):
                        search_org_to_state(org_input, "meta")

        exact_org, total_pages, org_url = render_org_resolution("meta", org_input)

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
                    "--target-url", st.session_state.get("meta_org_url", ""),
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
        "제공기관명을 입력합니다. 괄호 안 기관 구분값은 직접 입력하지 않아도 됩니다.",
        "포털 목록에서 실제 제공기관명을 확인하고, 수집 시 URL을 다시 검증합니다.",
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
                with st.spinner("기관명 확인 중입니다..."):
                    search_org_to_state(org_input, "stats")

    exact_org, total_pages, org_url = render_org_resolution("stats", org_input)

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
                "--target-url", st.session_state.get("stats_org_url", ""),
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
        "제공기관명을 입력합니다. 괄호 안 기관 구분값은 직접 입력하지 않아도 됩니다.",
        "포털 목록에서 실제 제공기관명을 확인하고, 수집 시 URL을 자동 탐지합니다.",
        "crawler_data.py의 현재데이터/과거데이터 다운로드 로직은 그대로 실행합니다.",
    ])

    st.markdown("**▪ 제공기관명 입력**")
    col_input, col_btn = st.columns([4, 1])
    with col_input:
        inst_input = st.text_input(
            "제공기관",
            label_visibility="collapsed",
            placeholder="예: 한국중부발전, 서울문화재단",
            key="download_inst",
        )
    with col_btn:
        if st.button("검색", icon=":material/search:", use_container_width=True, key="search_org_download"):
            if not inst_input.strip():
                st.warning("제공기관명을 입력해주세요.")
            else:
                with st.spinner("기관명 확인 중입니다..."):
                    search_org_to_state(inst_input, "download")

    exact_org, total_pages, org_url = render_org_resolution("download", inst_input)

    headless = st.checkbox("브라우저 숨김 실행", value=True, key="download_headless")

    if st.button("파일데이터 다운로드 시작", type="primary", use_container_width=True, key="start_download"):
        if not inst_input.strip() and not exact_org:
            st.error("제공기관명을 입력해주세요.")
        else:
            # 검색 버튼을 누르지 않아도 수집 시작 시 URL을 자동 확정합니다.
            if not exact_org or not org_url:
                with st.spinner("기관명과 파일데이터 URL을 자동 확인 중입니다..."):
                    search_org_to_state(inst_input, "download")
                exact_org = st.session_state.get("download_org_exact", "")
                org_url = st.session_state.get("download_org_url", "")
                total_pages = st.session_state.get("download_org_pages", 0)

            org_to_run = exact_org or inst_input.strip()
            # 정확한 기관 후보를 찾지 못한 경우 org_url은 keyword 검색 URL일 수 있으므로
            # runner에 빈 URL을 넘겨 다시 안전하게 해석하게 한다.
            target_url = org_url if total_pages > 0 else ""

            task_dir = create_task_dir("downloads", org_to_run)
            result_json = task_dir / "result.json"
            cmd = python_cmd(
                "download_runner.py",
                "--inst-name", org_to_run,
                "--org-url", target_url,
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
