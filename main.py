import os
import re
import time
import random
import urllib.parse
import requests
import pandas as pd
from bs4 import BeautifulSoup
import streamlit as st
from io import BytesIO
import math
import subprocess
import sys
import asyncio
import httpx

from streamlit_option_menu import option_menu
from crawler import collect_file_data_from_url
from crawler_data import main as run_file_download_crawler
import page1_org_metadata
from streamlit_task_ui import start_task, request_stop, render_task_status, get_task_state, is_task_running, clear_task

# ==========================================
# 0. 전역 변수 및 설정
# ==========================================
TARGET_METADATA_KEYS = [
    "파일데이터명", "분류체계", "제공기관", "관리부서명", "관리부서 전화번호",
    "보유근거", "수집방법", "업데이트 주기", "차기 등록 예정일", "매체유형",
    "전체 행", "확장자", "키워드", "데이터 한계", "다운로드(바로가기)",
    "등록일", "수정일", "제공형태", "설명", "기타 유의사항",
    "공간범위", "시간범위", "비용부과유무", "비용부과기준 및 단위", "이용허락범위"
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
    "이용허락범위", "다운로드(바로가기)", "상세페이지 URL"
]

# ==========================================
# 1. 크롤링 핵심 함수
# ==========================================
BASE_URL = "https://www.data.go.kr"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def get_soup(url, max_retries=3):
    for attempt in range(max_retries):
        try:
            time.sleep(random.uniform(0.5, 1.2))
            res = requests.get(url, headers=HEADERS, timeout=20)
            res.raise_for_status()
            return BeautifulSoup(res.text, "lxml")
        except Exception as e:
            if attempt == max_retries - 1: raise e
            time.sleep(random.uniform(2, 4))

def get_total_pages(search_org="", per_page=10):
    base_list_url = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"
    encoded_org = urllib.parse.quote(search_org) if search_org else ""
    list_url = f"{base_list_url}?dType=FILE&sort=updtDt&currentPage=1&perPage={per_page}"
    if search_org: list_url += f"&org={encoded_org}"
    try:
        soup = get_soup(list_url)
        page_numbers = []
        pagination = soup.select_one("nav.pagination, div.pagination, .page")
        if pagination:
            for a in pagination.find_all("a"):
                num_text = re.sub(r'\D', '', a.get_text())
                if num_text: page_numbers.append(int(num_text))
                onclick = a.get("onclick", "")
                nums_in_onclick = re.findall(r'\d+', onclick)
                if nums_in_onclick: page_numbers.extend([int(n) for n in nums_in_onclick])
                href = a.get("href", "")
                nums_in_href = re.findall(r'currentPage=(\d+)', href)
                if nums_in_href: page_numbers.extend([int(n) for n in nums_in_href])
        if page_numbers: return max(page_numbers)
        if soup.select("a[href*='/data/']"): return 1
    except: pass
    return 0

def find_valid_org_name(user_input):
    """(주) 기호 오류를 자동으로 잡아주는 풀프루프 함수"""
    base = user_input.strip()
    variations = [
        base, base + "㈜", base + "(주)", "㈜" + base, "(주)" + base,
        base.replace("(주)", "㈜"), base.replace("㈜", "(주)")
    ]
    variations = list(dict.fromkeys(variations))
    
    for var in variations:
        pages = get_total_pages(var)
        if pages > 0:
            return var, pages
    return base, 0

def format_tel_no(tel):
    tel = re.sub(r"\D", "", str(tel))
    if len(tel) == 8: return f"{tel[:4]}-{tel[4:]}"
    if len(tel) == 9: return f"{tel[:2]}-{tel[2:5]}-{tel[5:]}"
    if len(tel) == 10:
        if tel.startswith("02"): return f"{tel[:2]}-{tel[2:6]}-{tel[6:]}"
        return f"{tel[:3]}-{tel[3:6]}-{tel[6:]}"
    if len(tel) == 11: return f"{tel[:3]}-{tel[3:7]}-{tel[7:]}"
    return tel

def collect_one_detail_page(url):
    metadata = {key: "" for key in TARGET_METADATA_KEYS}
    metadata["상세페이지 URL"] = url
    metadata["컬럼목록"] = ""
    try:
        soup = get_soup(url)
        target_table = next((table for table in soup.select("table") if "파일데이터명" in str(table)), None)
        if target_table:
            for tr in target_table.select("tr"):
                cells = tr.find_all(["th", "td"], recursive=False)
                if not cells: cells = tr.find_all(["th", "td"])
                i = 0
                while i < len(cells) - 1:
                    key = re.sub(r"\s+", "", cells[i].get_text()).replace(":", "").replace("*", "")
                    value = re.sub(r"\s+", " ", cells[i+1].get_text()).strip()
                    mapped_key = METADATA_KEY_MAP.get(key)
                    if mapped_key in metadata: metadata[mapped_key] = value
                    i += 2
                    
        if not metadata.get("관리부서 전화번호"):
            tel_tag = soup.select_one("#telNo, #telNo1")
            if tel_tag:
                tel_text = tel_tag.get_text(strip=True)
                if tel_text: metadata["관리부서 전화번호"] = tel_text

        if not metadata.get("관리부서 전화번호"):
            html_text = str(soup)
            tel_match = re.search(r"var\s+telNo\s*=\s*['\"]([^'\"]+)['\"]", html_text)
            if tel_match: metadata["관리부서 전화번호"] = format_tel_no(tel_match.group(1))

        wrap = soup.select_one("#column-def-table-wrap")
        if wrap:
            for table in wrap.select("table"):
                if "항목명" not in str(table): continue
                trs = table.select("tr")
                if len(trs) > 1:
                    headers = [re.sub(r"\s+", "", th.get_text()) for th in trs[0].select("th, td")]
                    item_idx = next((i for i, h in enumerate(headers) if "항목명" in h), -1)
                    if item_idx != -1:
                        cols = [re.sub(r"\s+", " ", tr.select("th, td")[item_idx].get_text()).strip() for tr in trs[1:] if len(tr.select("th, td")) > item_idx]
                        metadata["컬럼목록"] = ", ".join(list(dict.fromkeys([c for c in cols if c and c not in ["정보시스템명", "DB명", "Table명", "코드"]])))
                        break
    except: pass
    return metadata


# ==========================================
# 추가: 기관명 자동 교정(풀프루프) 함수
# ==========================================
def find_valid_org_name(user_input):
    """
    사용자가 '(주)'를 빼거나 다르게 입력해도
    올바른 기관명을 자동으로 찾아주는 함수
    """
    base = user_input.strip()
    
    # 시도해볼 가능한 모든 이름 조합 (경우의 수)
    variations = [
        base,                          # 1. 입력한 그대로 (예: 한국남동발전)
        base + "㈜",                     # 2. 뒤에 특수기호 붙이기 (예: 한국남동발전㈜)
        base + "(주)",                   # 3. 뒤에 한글 붙이기
        "㈜" + base,                     # 4. 앞에 특수기호 (예: ㈜지음지식서비스)
        "(주)" + base,                   # 5. 앞에 한글 붙이기
        base.replace("(주)", "㈜"),      # 6. 괄호를 특수기호로 변환
        base.replace("㈜", "(주)")       # 7. 특수기호를 괄호로 변환
    ]
    
    # 중복 제거 (순서 유지)
    variations = list(dict.fromkeys(variations))
    
    for var in variations:
        pages = get_total_pages(var)
        if pages > 0:
            return var, pages # 성공하면 '정확한 기관명'과 '페이지 수' 반환
            
    return base, 0 # 다 실패하면 원래 이름과 0 반환


# ==========================================
# 1-0. 백그라운드 작업용 실행 함수
# ==========================================
def run_full_metadata_task(status_callback=None, stop_event=None):
    """crawler_metadata.py 전체 수집을 백그라운드에서 실행하고 stdout을 진행 로그로 표시합니다."""
    cmd = [sys.executable, "crawler_metadata.py"]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    if status_callback:
        status_callback("전체 메타데이터 수집 엔진을 실행합니다.")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
        bufsize=1,
        env=env,
    )

    last_current = 0
    last_total = 0
    try:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue

            # crawler_metadata.py의 진행 로그: [⭐️DETAIL]  50/1000 형태를 파싱
            m = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if m:
                last_current = int(m.group(1))
                last_total = int(m.group(2))

            if status_callback:
                status_callback(line, current=last_current or None, total=last_total or None)

            if stop_event and stop_event.is_set():
                if status_callback:
                    status_callback("중지 요청 감지: 전체 메타데이터 수집 프로세스를 종료합니다.", level="warning")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                return {"returncode": process.returncode, "stopped": True}

        returncode = process.wait()
        if returncode != 0:
            raise RuntimeError(f"crawler_metadata.py 실행 실패: returncode={returncode}")
        return {"returncode": returncode, "stopped": False}
    finally:
        if stop_event and stop_event.is_set() and process.poll() is None:
            process.terminate()


def run_stats_task(target_url, status_callback=None, stop_event=None):
    """기관별 조회수/다운로드 수 수집 작업."""
    return collect_file_data_from_url(target_url, status_callback=status_callback, stop_event=stop_event)


# ==========================================
# 1-1. 파일데이터 다운로드 페이지 함수
# ==========================================
def render_file_download_page():
    """crawler_data.py를 Streamlit 내부에서 직접 호출하는 3번 메뉴 화면."""
    st.markdown("""
    <div style="border-left: 5px solid #1F2937; padding-left: 15px; margin-bottom: 20px;">
        <span style="font-size: 26px; font-weight: 800; color: #1F2937;">기관별 포털 파일데이터 다운로드 크롤러</span>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    guide_html_menu3 = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;"><b>기관명</b>과 공공데이터포털 <b>기관별 파일데이터 페이지 URL</b>을 입력합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;"><b>[다운로드 시작]</b>을 누르면 진행상황이 실시간으로 표시됩니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">수집 완료 후 생성된 <b>ZIP 파일</b>을 다운로드합니다. 필요 시 <b>[중지]</b>로 작업을 종료합니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html_menu3, unsafe_allow_html=True)

    st.info("EXE 실행/config.json 생성 방식은 제거되었습니다. 이 메뉴는 crawler_data.py를 직접 import하여 실행합니다.")

    st.markdown("**▪&nbsp; 기관명 입력** (예: 한국중부발전(주))")
    inst_name = st.text_input(
        "기관명(파일데이터 다운로드)",
        label_visibility="collapsed",
        placeholder="기관명을 입력하세요. 예: 한국중부발전(주)",
        key="download_inst_name"
    )

    st.markdown("**▪&nbsp; 기관별 파일데이터 페이지 URL 입력**")
    org_url = st.text_input(
        "기관 URL(파일데이터 다운로드)",
        label_visibility="collapsed",
        placeholder="공공데이터포털 기관별 파일데이터 페이지 URL을 입력하세요.",
        key="download_org_url"
    )

    task_key = "task_file_download"
    col_run, col_stop, col_hint = st.columns([1, 1, 3], vertical_alignment="center")
    with col_run:
        run_download = st.button(
            "다운로드 시작",
            type="primary",
            use_container_width=True,
            key="run_download_crawler",
            disabled=is_task_running(task_key),
        )
    with col_stop:
        stop_download = st.button(
            "중지",
            use_container_width=True,
            key="stop_download_crawler",
            disabled=not is_task_running(task_key),
        )
    with col_hint:
        st.caption("기존 crawler_data.py의 현재데이터/과거데이터 다운로드 로직과 폴더 구조를 유지합니다.")

    if stop_download:
        request_stop(task_key)
        st.warning("중지 요청을 보냈습니다. 현재 처리 중인 다운로드를 마친 뒤 종료합니다.")

    if run_download:
        if not inst_name.strip() or not org_url.strip():
            st.error("기관명과 기관별 파일데이터 페이지 URL을 모두 입력해주세요.")
        else:
            if "data.go.kr" not in org_url:
                st.warning("입력한 URL이 공공데이터포털 주소인지 확인해주세요. 그래도 실행은 가능합니다.")
            clear_task(task_key)
            start_task(
                task_key,
                run_file_download_crawler,
                inst_name=inst_name.strip(),
                org_url=org_url.strip(),
                headless=True,
                task_name=f"{inst_name.strip()} 파일데이터 다운로드",
            )
            st.rerun()

    state = render_task_status(task_key, title="파일데이터 다운로드 진행상황")
    if state and state.get("status") in ["done", "stopped"]:
        zip_path = state.get("result")
        if zip_path and os.path.exists(zip_path):
            with open(zip_path, "rb") as f:
                st.download_button(
                    label="📥 다운로드 결과 ZIP 받기",
                    data=f,
                    file_name=os.path.basename(zip_path),
                    mime="application/zip",
                    use_container_width=True,
                )
        elif state.get("status") == "done":
            st.warning("크롤러 실행은 완료되었지만 ZIP 파일 경로를 찾지 못했습니다. 작업 폴더를 확인해주세요.")


# ==========================================
# 2. 웹 UI 및 로직 (Streamlit 버전)
# ==========================================
st.set_page_config(page_title="공공데이터 크롤러", page_icon="🏢", layout="wide") # wide 로 변경

# 디테일한 디자인을 위한 CSS
st.markdown("""
    <style>
    .title-spacer { margin-bottom: 50px; }
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
""", unsafe_allow_html=True)

if "total_pages" not in st.session_state:
    st.session_state.total_pages = 0

# ==========================================
# 3. 왼쪽 사이드바 메뉴
# ==========================================
with st.sidebar:
    # ☰ 햄버거 바 + 메뉴 텍스트 조합 상단 배치
    simple_menu_html = """
    <style>
    .menu-container { display: flex; align-items: center; gap: 12px; margin-top: 10px; margin-bottom: 25px; padding-left: 8px; }
    .hamburger-icon { font-size: 1.5rem; color: #31333F; line-height: 1; }
    .menu-text { font-size: 1.25rem; font-weight: 600; color: #31333F; }
    </style>
    <div class="menu-container">
        <div class="hamburger-icon">☰</div>
        <div class="menu-text">메뉴</div>
    </div>
    """
    st.markdown(simple_menu_html, unsafe_allow_html=True)

    # 깔끔한 버튼형 내비게이션 메뉴 (요청하신 새로운 제목 반영)
    menu = option_menu(
        menu_title=None, 
        options=["메타데이터 크롤링", "조회수 및 다운로드 수", "파일데이터 다운로드"], # 딱 원하시던 제목 리스트!
        icons=["database", "bar-chart-line", "cloud-download"], 
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "#6B7280", "font-size": "18px"}, 
            "nav-link": {"font-size": "14.5px", "text-align": "left", "margin":"6px 0px", "--hover-color": "#F3F4F6", "border-radius": "8px"},
            "nav-link-selected": {"background-color": "#EF4444", "color": "white", "font-weight": "bold", "border-radius": "8px"}, 
        }
    )

# ==========================================
# 📄 [페이지 1] 메타데이터 크롤링
# ==========================================
if menu == "메타데이터 크롤링":
    #st.title("공공데이터 포털 메타데이터 크롤링")

    st.markdown("""
    <div style="border-left: 5px solid #1F2937; padding-left: 15px; margin-bottom: 20px;">
        <span style="font-size: 26px; font-weight: 800; color: #1F2937;">공공데이터 포털 메타데이터 크롤링</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<div class='title-spacer'></div>", unsafe_allow_html=True)

    # 두 개의 탭으로 나눔
    tab1, tab2 = st.tabs(["1️⃣ 전체 데이터 수집", "2️⃣ 기관별 수집"])

    # ----------------------------------------------------
    # 1: 전체 데이터 수집 (crawler_metadata.py 연동)
    # ----------------------------------------------------
    with tab1:

        # 1번 탭용 디자인 가이드 박스
        guide_html_tab1 = """
        <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
            <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
            <div style="display: flex; gap: 15px;">
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">여기에 설명을 입력하세요.</div>
                </div>
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">여기에 설명을 입력하세요.</div>
                </div>
                <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                    <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                    <div style="font-size: 14px; color: #475569; line-height: 1.5;">여기에 설명을 입력하세요.</div>
                </div>
            </div>
        </div>
        """
        st.markdown(guide_html_tab1, unsafe_allow_html=True)
        
       
        
        task_key = "task_full_metadata"
        col_start, col_stop, col_note = st.columns([1, 1, 3], vertical_alignment="center")
        with col_start:
            start_full = st.button(
                "전체 수집 시작",
                type="primary",
                use_container_width=True,
                disabled=is_task_running(task_key),
                key="start_full_metadata",
            )
        with col_stop:
            stop_full = st.button(
                "중지",
                use_container_width=True,
                disabled=not is_task_running(task_key),
                key="stop_full_metadata",
            )
        with col_note:
            st.caption("crawler_metadata.py 전체 수집 엔진을 실행하고 stdout 로그를 진행상황으로 표시합니다.")

        if stop_full:
            request_stop(task_key)
            st.warning("중지 요청을 보냈습니다. 현재 처리 중인 요청을 마친 뒤 종료합니다.")

        if start_full:
            clear_task(task_key)
            start_task(
                task_key,
                run_full_metadata_task,
                task_name="공공데이터포털 전체 메타데이터 수집",
            )
            st.rerun()

        render_task_status(task_key, title="전체 메타데이터 수집 진행상황")

    # ----------------------------------------------------
    # 2: 기관별 파일데이터 정보 크롤링
    # ----------------------------------------------------
    with tab2:
       # 분리한 파일(page1_org_metadata.py)의 탭 렌더링 함수 실행
        page1_org_metadata.render_tab2(
            get_soup, 
            find_valid_org_name, 
            format_tel_no, 
            BASE_URL, 
            HEADERS, 
            ALL_SELECTABLE_COLUMNS, 
            TARGET_METADATA_KEYS, 
            METADATA_KEY_MAP
        )

# ==========================================
# 📄 [페이지 2] 조회수 및 다운로드 수 (2단계 UI 적용)
# ==========================================
elif menu == "조회수 및 다운로드 수":
    #st.title("기관별 데이터 조회수 및 다운로드 수")
    st.markdown("""
    <div style="border-left: 5px solid #1F2937; padding-left: 15px; margin-bottom: 20px;">
        <span style="font-size: 26px; font-weight: 800; color: #1F2937;">기관별 데이터 조회수 및 다운로드 수</span>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    # 2번 디자인 가이드 박스 추가
    guide_html_menu2 = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">검색창에 <b>제공기관명</b>을 입력하고 [검색]을 누릅니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">검색 결과를 확인한 뒤 <b>[추출]</b> 버튼을 누릅니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">수집이 완료되면 생성된 <b>엑셀 파일</b>을 다운로드합니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html_menu2, unsafe_allow_html=True)
    
    # 2번 메뉴 전용 세션 상태 초기화 (3번 메뉴와 충돌 방지)
    if "total_pages2" not in st.session_state:
        st.session_state.total_pages2 = 0
    if "target_org2" not in st.session_state:
        st.session_state.target_org2 = ""

    # 1. 기관명 입력 및 검색 UI
    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주))")
    col1, col2 = st.columns([4, 1]) 

    with col1:
        org_input = st.text_input(
            "제공기관", 
            label_visibility="collapsed", 
            placeholder="기관명을 입력하면 해당 기관의 데이터 목록과 조회수/다운로드 수를 수집합니다."
        )
        
    with col2:
        search_clicked = st.button("검색", icon=":material/search:", use_container_width=True, key="search_btn2")

    # 검색 로직
    if search_clicked:
        if not org_input.strip():
            st.warning("제공기관명을 입력해주세요!")
        else:
            with st.spinner(f"'{org_input}'에 해당하는 기관명을 찾고 있습니다..."):
                exact_org_name, total_pages = find_valid_org_name(org_input)
                
                st.session_state.total_pages2 = total_pages
                st.session_state.target_org2 = exact_org_name 
                
            if total_pages == 0:
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인해주세요.")
            else:
                if exact_org_name != org_input.strip():
                    st.info(f"💡 '{exact_org_name}'(으)로 자동 변환하여 검색했습니다.")
                

    # 2. 검색 결과가 있을 때만 '추출' UI 표시
    if st.session_state.total_pages2 > 0:
        st.markdown("---")
        
        task_key = "task_stats"
        col_info, col_extract, col_stop = st.columns([3, 1, 1], vertical_alignment="center")
        
        with col_info:
            st.success(f"✅ 검색 완료! 총 {st.session_state.total_pages2}페이지(최대 {st.session_state.total_pages2 * 10}건)의 데이터가 발견되었습니다.")
            
        with col_extract:
            run_clicked = st.button(
                "추출 시작",
                type="primary",
                use_container_width=True,
                key="extract_btn2",
                disabled=is_task_running(task_key),
            )
        with col_stop:
            stop_clicked = st.button(
                "중지",
                use_container_width=True,
                key="stop_btn2",
                disabled=not is_task_running(task_key),
            )

        if stop_clicked:
            request_stop(task_key)
            st.warning("중지 요청을 보냈습니다. 현재 처리 중인 페이지를 마친 뒤 종료합니다.")

        # 추출 버튼 클릭 시 crawler.py 백그라운드 실행
        if run_clicked:
            org = st.session_state.target_org2
            encoded_org = urllib.parse.quote(org)
            target_url = f"https://www.data.go.kr/tcs/dss/selectDataSetList.do?org={encoded_org}"
            clear_task(task_key)
            start_task(
                task_key,
                run_stats_task,
                target_url,
                task_name=f"{org} 조회수/다운로드 수 수집",
            )
            st.rerun()

        state = render_task_status(task_key, title="조회수 및 다운로드 수 수집 진행상황")
        if state and state.get("status") in ["done", "stopped"] and state.get("result") is not None:
            df = state.get("result")
            if df.empty:
                st.warning("수집된 데이터가 없습니다.")
            else:
                st.success(f"🎉 수집 결과: 총 {len(df)}건")
                st.dataframe(df, use_container_width=True)

                output = BytesIO()
                with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                    df.to_excel(writer, index=False, sheet_name="FILE_집계")
                output.seek(0)

                org = st.session_state.target_org2
                safe_org_name = org.replace("(", "_").replace(")", "")
                st.download_button(
                    label="📥 엑셀(Excel) 파일 다운로드",
                    data=output,
                    file_name=f"공공데이터_{safe_org_name}_조회수_다운로드수_{time.strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )



# ==========================================
# 📄 [페이지 3] 기관별 포털 파일데이터 다운로드 크롤러
# ==========================================
elif menu == "파일데이터 다운로드":
    render_file_download_page()