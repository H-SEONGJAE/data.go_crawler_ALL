# -*- coding: utf-8 -*-
"""
crawler_metadata_test_v7_4_5000_column_placeholder.py

공공데이터포털 메타데이터 수집 테스트용 스크립트 v7.2-fixed
- v4 FAST 파싱 로직 유지
- 5000건 수집 기준
- 목록 URL 먼저 수집 후 상세 페이지는 httpx 병렬 처리
- 메인 실행 중 실패 URL 즉시 재수집은 비활성화
- 403/429 실패는 실패로그.xlsx에만 기록하고, 같은 파일의 RETRY_FAILED 모드에서 재수집
- v6.9: 본문 내 단순 숫자 429/403 오탐 제거
- v7.2: 본문 차단 감지 보수화, /dataset 상세 URL 인식, CMD 진행 화면 간소화
- v7.1: '접근이 제한' 일반 문구 오탐 제거, EMPTY_OR_SHORT_HTML 재수집 포함, 최종 실패로그 URL 중복 제거
- v7.2: 본문 차단 감지 보수화, /dataset 상세 URL 인식, CMD 진행 화면 간소화
- v7.5: 컬럼정보.xlsx 별도 출력 제거, 메타데이터 수집 및 컬럼목록 내부 생성은 유지
- v7.6: 관리부서 전화번호 수집 시 오픈API 관리기관/포털 대표번호(1566-0025) 오염 방지
- v7.7: telNo span/script 방식의 관리부서 전화번호 추출 보강
- 차단 가능성을 낮추기 위한 안전장치 포함
  1) 상세 동시 처리 수 제한
  2) 이미지/폰트/CSS/미디어 리소스 차단
  3) 요청 간 짧은 랜덤 지터
  4) 403/429/차단 문구 감지
  5) 실패 시 백오프 재시도

실행 전 최초 1회:
    pip install playwright pandas openpyxl beautifulsoup4 lxml
    playwright install chromium

실행:
    python crawler_metadata_test_v7_2_5000_safe_detect_ui.py
"""

import os
import re
import json
import time
import shutil
import random
import asyncio
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl, urlencode, urlunparse

import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright.sync_api import sync_playwright

try:
    import httpx
except ImportError:
    httpx = None

# ==========================================================
# 0. 사용자 설정
# ==========================================================

USE_CONFIG_JSON = False
CONFIG_PATH = "config.json"

# 실행 모드
# MAIN         : 5000건 빠른 메인 수집만 실행
# RETRY_FAILED : 실패로그.xlsx 기준 Playwright 재수집만 실행
# BOTH         : MAIN 실행 후 3분 대기하고 RETRY_FAILED까지 이어서 실행
RUN_MODE = "BOTH"

JOB_NAME = "공공데이터포털_메타데이터"

TARGET_URL = (
    "https://www.data.go.kr/tcs/dss/selectDataSetList.do?dType=FILE&keyword=&detailKeyword=&publicDataPk=&recmSe=N&detailText=&relatedKeyword=&commaNotInData=&commaAndData=&commaOrData=&must_not=&tabId=&dataSetCoreTf=&coreDataNm=&sort=updtDt&relRadio=&orgFullName=&orgFilter=&org=&orgSearch=&currentPage=1&perPage=10&brm=&instt=&svcType=&kwrdArray=&extsn=&coreDataNmArray=&operator=AND&pblonsipScopeCode=PBDE07"
)

# 5000건 수집 기준
MAX_DETAIL_ITEMS = 1000000

# perPage=1000 테스트 기준입니다.
# 서버가 perPage=1000을 허용하면 20,000건 수집에 약 20페이지가 필요합니다.
# 서버가 100건 단위로 제한해도 기존처럼 200페이지까지 순차 수집합니다.
MAX_PAGES = 1000

HEADLESS = True
PAGE_TIMEOUT_MS = 15000
DETAIL_TIMEOUT_MS = 15000

# v7.2-fixed:
# - status=200인데 HTML 길이가 너무 짧은 경우는 포털의 짧은 안내/전환 응답일 가능성이 있어
#   같은 httpx 요청만 반복하지 않고 대체 URL(/dataset/{id}/fileData.do?lang=ko)과 Playwright 회수 대상으로 넘깁니다.
SHORT_HTML_MIN_LEN = 500
PLAYWRIGHT_FALLBACK_FOR_SHORT_HTML = True

SOURCE_FILE_LABEL = "실시간수집"
OUTPUT_DIR = None

# ==========================================================
# 0-1. 속도/차단 방지 균형 옵션
# ==========================================================

# 목록 페이지당 요청 건수입니다.
# 홈페이지 UI에서 보이는 최대 건수와 별개로 URL의 perPage 파라미터에 직접 반영됩니다.
# 실행 로그가 page 01 +1000건이면 서버가 1000건 요청을 허용한 것이고,
# +100건이면 서버가 100건으로 제한한 것입니다.
LIST_PER_PAGE = 1000

# 상세페이지 동시 처리 수
# 3~4 권장. 5 이상은 빨라질 수 있지만 차단/실패 가능성이 올라감
DETAIL_CONCURRENCY = 30

# 이미지/폰트/CSS/미디어 차단
BLOCK_RESOURCE_TYPES = True

# networkidle은 느려서 기본 미사용
USE_NETWORKIDLE_WAIT = False

# 짧은 지터: 속도 저하를 크게 만들지 않으면서 요청이 완전히 균일하게 몰리는 것 방지
DETAIL_JITTER_MIN_SEC = 0.1
DETAIL_JITTER_MAX_SEC = 0.3

# 목록 페이지 간 짧은 지터
PAGE_JITTER_MIN_SEC = 0.2
PAGE_JITTER_MAX_SEC = 0.6

# 실패 재시도
MAX_DETAIL_RETRIES = 3
RETRY_BASE_DELAY_SEC = 3.0

# 연속 차단/오류 감지 시 전체 중단 기준
MAX_BLOCK_SIGNALS = 50

# 429/403 감지 시 전체 worker가 잠깐 쉬는 adaptive cooldown.
# v5.3에서는 긴 쿨다운으로 전체가 오래 멈추지 않도록 짧게 잡고,
# 차단 URL은 즉시 재시도하지 않고 후순위 재시도 큐로 넘깁니다.
BLOCK_COOLDOWN_BASE_SEC = 4.0
BLOCK_COOLDOWN_MAX_SEC = 12.0

# MAIN 모드에서는 속도를 우선하기 위해 쿨다운을 끌 수 있습니다.
# False이면 429/403 발생 시 실패로그에는 남기지만 전체 worker를 멈추지 않습니다.
ENABLE_MAIN_COOLDOWN = False

# 429/403 발생 URL 처리 방식
# True: 즉시 여러 번 재시도하지 않고, 후순위 큐로 넘겨 메인 수집 이후 재수집
DEFER_BLOCKED_URLS = True
RETRY_DEFERRED_AFTER_MAIN = True
DEFERRED_RETRY_CONCURRENCY = 2
DEFERRED_RETRY_WAIT_MIN_SEC = 12.0
DEFERRED_RETRY_WAIT_MAX_SEC = 20.0

# v6.2 핵심:
# 403/429가 난 URL은 같은 httpx 방식으로 다시 때리지 않고 바로 Playwright 회수 대상으로 넘깁니다.
# True 권장. False로 바꾸면 v6.1처럼 후순위 httpx 재시도 후 Playwright fallback으로 갑니다.
SKIP_DEFERRED_HTTPX_RETRY = True

# True면 메인 실행 중 Playwright 회수까지 하지 않고 실패로그에만 기록합니다.
# 1000건 메인 수집 속도를 우선하기 위해 True로 둡니다.
# 실패 URL은 같은 파일의 RETRY_FAILED 모드에서 3분 뒤 재수집합니다.
SAVE_FAILED_URLS_ONLY = True

# 메인 실행 중 Playwright 즉시 재수집은 하지 않습니다.
# 즉시 재수집은 429 제한이 풀리지 않아 실패하는 경우가 많고, 전체 시간만 늘릴 수 있습니다.
PLAYWRIGHT_FALLBACK_FOR_FAILED = False
PLAYWRIGHT_FALLBACK_CONCURRENCY = 1
PLAYWRIGHT_FALLBACK_WAIT_MIN_SEC = 3.0
PLAYWRIGHT_FALLBACK_WAIT_MAX_SEC = 8.0

# ==========================================================
# 0-2. 실패로그 Playwright 재수집 옵션
# ==========================================================

# RETRY_FAILED / BOTH 모드에서 사용합니다.
# None이면 현재 JOB_NAME 기준 output 폴더의 실패로그.xlsx를 사용합니다.
RETRY_FAIL_LOG_PATH = None
RETRY_EXISTING_METADATA_PATH = None
RETRY_EXISTING_COLUMNS_PATH = None

# BOTH 모드에서는 MAIN 종료 후 무조건 3분 대기하고 재수집합니다.
BOTH_MODE_WAIT_SEC = 180

# RETRY_FAILED 단독 실행 시에는 실패로그 마지막 수집시각 기준으로 3분이 안 지났으면 남은 시간만 대기합니다.
RETRY_AUTO_WAIT = True
RETRY_WAIT_AFTER_FAIL_SEC = 180

# 실패 URL 재수집은 병렬 없이 Playwright page 1개로 순차 처리합니다.
RETRY_MAX_RETRIES_PER_URL = 2
RETRY_URL_DELAY_MIN_SEC = 3.0
RETRY_URL_DELAY_MAX_SEC = 5.0
RETRY_BLOCK_DELAY_MIN_SEC = 30.0
RETRY_BLOCK_DELAY_MAX_SEC = 60.0
RETRY_HEADLESS = True
RETRY_MERGE_WITH_EXISTING = True
RETRY_SOURCE_FILE_LABEL = "실패로그_재수집"

# 저장
MAKE_ZIP = False
VERBOSE_DETAIL_LOG = False

# CMD 진행 화면 옵션
# 상세 수집은 너무 많은 줄을 출력하지 않고 N건 단위로 요약 출력합니다.
DETAIL_PROGRESS_EVERY = 50
SHOW_EACH_SUCCESS = False
SHOW_EACH_FAILURE = True

# 중간 저장 주기. 0이면 중간 저장 안 함
# 30,000건 기준 중간 저장 부담을 줄이기 위해 5,000건 단위로 조정했습니다.
# 안정화 후 속도를 더 우선하면 0으로 변경해도 됩니다.
CHECKPOINT_EVERY = 5000

# ==========================================================
# 1. 목표 출력 스키마
# ==========================================================

TARGET_METADATA_COLUMNS = [
    "최종순번",
    "파일데이터명",
    "제공기관",
    "분류체계",
    "확장자",
    "전체 행",
    "키워드",
    "설명",
    "컬럼목록",
    "상세페이지 URL",
    "관리부서명",
    "관리부서 전화번호",
    "보유근거",
    "수집방법",
    "업데이트 주기",
    "차기 등록 예정일",
    "매체유형",
    "데이터 한계",
    "조회수",
    "다운로드(바로가기)",
    "등록일",
    "수정일",
    "제공형태",
    "기타 유의사항",
    "공간범위",
    "시간범위",
    "비용부과유무",
    "비용부과기준 및 단위",
    "이용허락범위",
    "수집파일",
]

COLUMN_OUTPUT_COLUMNS = [
    "파일데이터명",
    "상세페이지 URL",
    "순번",
    "항목명",
    "항목설명",
    "데이터타입",
    "데이터 길이",
]

FAIL_COLUMNS = [
    "수집시각",
    "단계",
    "파일데이터명",
    "URL",
    "최종순번",
    "조회수",
    "다운로드(바로가기)",
    "오류",
    "Traceback",
]

LABEL_TO_TARGET = {
    "파일데이터명": "파일데이터명",
    "제목": "파일데이터명",
    "데이터명": "파일데이터명",
    "제공기관": "제공기관",
    "기관명": "제공기관",
    "분류체계": "분류체계",
    "분류": "분류체계",
    "확장자": "확장자",
    "파일형식": "확장자",
    "제공형식": "확장자",
    "전체행": "전체 행",
    "전체 행": "전체 행",
    "전체건수": "전체 행",
    "전체 건수": "전체 행",
    "데이터건수": "전체 행",
    "데이터 건수": "전체 행",
    "행수": "전체 행",
    "키워드": "키워드",
    "검색키워드": "키워드",
    "설명": "설명",
    "데이터설명": "설명",
    "파일데이터 설명": "설명",
    "관리부서명": "관리부서명",
    "관리부서": "관리부서명",
    "관리부서전화번호": "관리부서 전화번호",
    "관리부서 전화번호": "관리부서 전화번호",
    "관리부서 전화 번호": "관리부서 전화번호",
    "담당부서전화번호": "관리부서 전화번호",
    "담당부서 전화번호": "관리부서 전화번호",
    "담당부서 전화 번호": "관리부서 전화번호",
    "전화번호": "관리부서 전화번호",
    "보유근거": "보유근거",
    "수집방법": "수집방법",
    "업데이트주기": "업데이트 주기",
    "업데이트 주기": "업데이트 주기",
    "갱신주기": "업데이트 주기",
    "제공주기": "업데이트 주기",
    "차기등록예정일": "차기 등록 예정일",
    "차기 등록 예정일": "차기 등록 예정일",
    "차기등록일": "차기 등록 예정일",
    "매체유형": "매체유형",
    "매체 유형": "매체유형",
    "데이터한계": "데이터 한계",
    "데이터 한계": "데이터 한계",
    "다운로드": "다운로드(바로가기)",
    "다운로드수": "다운로드(바로가기)",
    "다운로드 수": "다운로드(바로가기)",
    "다운로드(바로가기)": "다운로드(바로가기)",
    "바로가기": "다운로드(바로가기)",
    "등록일": "등록일",
    "최초등록일": "등록일",
    "최초 등록일": "등록일",
    "수정일": "수정일",
    "최종수정일": "수정일",
    "최종 수정일": "수정일",
    "수정일자": "수정일",
    "제공형태": "제공형태",
    "제공 형태": "제공형태",
    "기타유의사항": "기타 유의사항",
    "기타 유의사항": "기타 유의사항",
    "유의사항": "기타 유의사항",
    "공간범위": "공간범위",
    "공간 범위": "공간범위",
    "시간범위": "시간범위",
    "시간 범위": "시간범위",
    "비용부과유무": "비용부과유무",
    "비용부과 유무": "비용부과유무",
    "비용부과기준및단위": "비용부과기준 및 단위",
    "비용부과 기준 및 단위": "비용부과기준 및 단위",
    "비용부과기준 및 단위": "비용부과기준 및 단위",
    "이용허락범위": "이용허락범위",
    "이용허락 범위": "이용허락범위",
}

COLUMN_LABEL_MAP = {
    "항목명": "항목명",
    "컬럼명": "항목명",
    "필드명": "항목명",
    "항목설명": "항목설명",
    "항목 설명": "항목설명",
    "컬럼설명": "항목설명",
    "컬럼 설명": "항목설명",
    "설명": "항목설명",
    "데이터타입": "데이터타입",
    "데이터 타입": "데이터타입",
    "타입": "데이터타입",
    "최대길이": "데이터 길이",
    "최대 길이": "데이터 길이",
    "데이터길이": "데이터 길이",
    "데이터 길이": "데이터 길이",
    "길이": "데이터 길이",
}

# ==========================================================
# 2. 공통 유틸
# ==========================================================

def load_settings():
    settings = {
        "job_name": JOB_NAME,
        "target_url": TARGET_URL,
        "max_pages": MAX_PAGES,
        "max_detail_items": MAX_DETAIL_ITEMS,
        "headless": HEADLESS,
        "source_file_label": SOURCE_FILE_LABEL,
        "list_per_page": LIST_PER_PAGE,
        "detail_concurrency": DETAIL_CONCURRENCY,
        "make_zip": MAKE_ZIP,
        "verbose_detail_log": VERBOSE_DETAIL_LOG,
    }

    if USE_CONFIG_JSON and os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)

        settings.update({
            "job_name": config.get("job_name", settings["job_name"]),
            "target_url": config.get("target_url", settings["target_url"]),
            "max_pages": int(config.get("max_pages", settings["max_pages"])),
            "max_detail_items": int(config.get("max_detail_items", settings["max_detail_items"])),
            "headless": bool(config.get("headless", settings["headless"])),
            "source_file_label": config.get("source_file_label", settings["source_file_label"]),
            "list_per_page": int(config.get("list_per_page", settings["list_per_page"])),
            "detail_concurrency": int(config.get("detail_concurrency", settings["detail_concurrency"])),
            "make_zip": bool(config.get("make_zip", settings["make_zip"])),
            "verbose_detail_log": bool(config.get("verbose_detail_log", settings["verbose_detail_log"])),
        })

    return settings

def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def norm_key(value):
    s = clean_text(value)
    s = re.sub(r"[\s:：·ㆍ\-_/\[\]\(\)]", "", s)
    return s

def clean_filename(value):
    text = clean_text(value)
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip() or "unnamed"

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def fmt_elapsed(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def print_section(title):
    print("\n" + "=" * 80)
    print(f"[{title}]")
    print("=" * 80)

def print_progress(prefix, done, total, ok=0, fail=0, extra="", start_time=None):
    total = max(int(total or 0), 1)
    pct = (done / total) * 100
    elapsed_txt = ""
    speed_txt = ""
    if start_time is not None:
        elapsed = time.perf_counter() - start_time
        elapsed_txt = f" | {fmt_elapsed(elapsed)}"
        speed = done / elapsed if elapsed > 0 else 0
        speed_txt = f" | {speed:.2f}건/s"

    msg = f"[{prefix}] {done:>5}/{total:<5} ({pct:5.1f}%) | 성공 {ok:,} | 실패 {fail:,}{speed_txt}{elapsed_txt}"
    if extra:
        msg += f" | {extra}"
    print(msg)

def make_output_dir(job_name):
    root = OUTPUT_DIR or f"{clean_filename(job_name)}_포털데이터"
    Path(root).mkdir(parents=True, exist_ok=True)
    return root

def absolute_url(base_url, href):
    if not href:
        return ""
    return urljoin(base_url, href)

def is_detail_url(url):
    if not url:
        return False
    u = url.lower()
    return (
        ("/data/" in u or "/dataset/" in u)
        and (
            "filedata.do" in u
            or "dataid=" in u
            or re.search(r"/(?:data|dataset)/\d+", u) is not None
        )
    )

def make_detail_url_candidates(url):
    """
    공공데이터포털 상세 URL은 /data/{id}/fileData.do 와
    /dataset/{id}/fileData.do?lang=ko 형태가 혼재됩니다.
    status=200인데 짧은 HTML만 오는 경우가 있어, httpx 단계에서도
    두 형태를 순차 시도합니다. 최종 저장 URL은 원 URL을 유지합니다.
    """
    url = clean_text(url)
    candidates = []

    if url:
        candidates.append(url)

    patterns = [
        r"/data/(\d+)/fileData\.do",
        r"/dataset/(\d+)/fileData\.do",
    ]
    data_id = ""
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            data_id = m.group(1)
            break

    if data_id:
        alt_urls = [
            f"https://www.data.go.kr/dataset/{data_id}/fileData.do?lang=ko",
            f"https://www.data.go.kr/data/{data_id}/fileData.do",
        ]
        for alt in alt_urls:
            if alt not in candidates:
                candidates.append(alt)

    return candidates

def is_short_html_error(err_text):
    err_text = str(err_text)
    return "EMPTY_OR_SHORT_HTML" in err_text or "RETRY_FAILED_EMPTY_OR_SHORT_HTML" in err_text

def optimize_list_url(url, per_page=100, current_page=None):
    if not url or is_detail_url(url):
        return url

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["perPage"] = str(per_page)

    if current_page is not None:
        query["currentPage"] = str(current_page)
    else:
        query.setdefault("currentPage", "1")

    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

def detect_formats(text):
    text = clean_text(text)
    upper = text.upper()
    formats = []

    for fmt in [
        "CSV", "JSON", "XML", "XLSX", "XLS", "PDF", "HWP", "HWPX", "TXT", "ZIP", "SHP",
        "MP4", "AVI", "MOV", "WMV", "JPG", "JPEG", "PNG", "GIF", "DOC", "DOCX", "PPT", "PPTX",
    ]:
        if re.search(rf"\b{fmt}\b", upper):
            formats.append(fmt)

    return formats

def clean_dataset_title(raw_title):
    s = clean_text(raw_title)

    if not s:
        return ""

    # 목록 카드에서 a 태그 전체 텍스트를 가져오면
    # "CSV JSON + XML 데이터명 Update 조회수 123 다운로드 45"처럼
    # 파일 형식 배지/상태 문구/목록 부가정보가 같이 섞일 수 있습니다.
    # 최종 파일데이터명에는 순수 목록명만 남기도록 앞뒤 배지와 상태 문구를 제거합니다.
    file_type_pattern = (
        r"CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP|"
        r"MP4|AVI|MOV|WMV|JPG|JPEG|PNG|GIF|DOCX|DOC|PPTX|PPT|"
        r"파일데이터|오픈API|API"
    )

    # 앞쪽 파일 형식 배지 반복 제거: "CSV JSON + XML 데이터명" -> "데이터명"
    s = re.sub(
        rf"^((?:{file_type_pattern})\s*(?:\+|,|/|\\|｜|·|ㆍ|-)?\s*)+",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # New / Update / 업데이트 같은 상태 문구 제거
    status_pattern = r"New|Update|Updated|업데이트|NEW|UPDATE"
    s = re.sub(rf"^\s*(?:{status_pattern})\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(rf"\s+(?:{status_pattern})\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bNew\b", "", s, flags=re.IGNORECASE)

    # 목록 카드 부가정보가 제목 뒤에 붙은 경우 제목 앞부분만 사용
    # 예: "한국가스공사_용수사용량 제공기관 한국가스공사 수정일 ... 조회수 2930 다운로드 638"
    cut_markers = [
        "제공기관", "분류체계", "수정일", "등록일", "조회수", "조회 수", "다운로드수", "다운로드 수", "다운로드", "키워드",
    ]
    cut_positions = [s.find(marker) for marker in cut_markers if s.find(marker) > 0]
    if cut_positions:
        s = s[:min(cut_positions)]

    # 부가정보 절단 후 끝에 남은 상태 문구도 한 번 더 제거
    s = re.sub(rf"\s+(?:{status_pattern})\s*$", "", s, flags=re.IGNORECASE)

    # 상세페이지 title 태그에 붙는 포털명 제거
    s = re.sub(r"\s*[|｜-]\s*공공데이터포털\s*$", "", s)

    # 전체가 괄호로 감싸진 경우만 바깥 괄호 제거
    s = clean_text(s)
    if len(s) >= 2 and ((s[0], s[-1]) in [("(", ")"), ("[", "]"), ("<", ">"), ("〈", "〉")]):
        s = clean_text(s[1:-1])

    return clean_text(s)

def strip_dataset_date_suffix(title):
    """
    상세페이지 파일데이터명에는 최신 기준일이 _YYYYMMDD 형태로 붙는 경우가 많습니다.
    목록명 비교/보정 시에는 이 접미사를 제거해 같은 데이터명인지 판단합니다.
    """
    s = clean_dataset_title(title)
    s = re.sub(r"_20\d{6}$", "", s)
    return clean_text(s)


def normalize_title_for_compare(title):
    s = strip_dataset_date_suffix(title)
    s = re.sub(r"\s+", "", s)
    return s.lower()

# ==========================================================
# 비정상 상세페이지/공통 메뉴 제목 오탐 방지
# ==========================================================
# 폐기/비정상 URL이 공공데이터포털의 데이터목록/공통 화면을 반환하는 경우,
# 상단 메뉴명이 파일데이터명으로 잘못 들어가는 것을 방지합니다.
COMMON_TITLE_CONTAINS_KEYWORDS = [
    # 길고 고유한 포털 공통 메뉴명만 부분 포함으로 제외합니다.
    "기업 공공데이터 문제해결 지원센터",
    "공공데이터 활용기업 문제해결 지원신청",
]

COMMON_TITLE_EXACT_KEYWORDS = [
    # 짧은 메뉴명은 실제 데이터명에 포함될 수 있으므로 정확히 일치할 때만 제외합니다.
    # 예: "회원가입"은 한국자산관리공사_온비드 회원가입 현황 같은 정상 데이터명에 포함됩니다.
    "데이터목록",
    "조건검색",
    "데이터찾기",
    "국가데이터맵",
    "데이터요청",
    "데이터활용",
    "정보공유",
    "이용안내",
    "사이트맵",
    "로그인",
    "회원가입",
    "ENGLISH",
    "공공데이터포털",
    "DATA.GO.KR",
]


def is_common_title_candidate(value):
    text = clean_text(value)
    if not text:
        return True

    # 길고 고유한 공통 메뉴명은 포함 여부로 제외
    if any(x in text for x in COMMON_TITLE_CONTAINS_KEYWORDS):
        return True

    compact = norm_key(text)
    bad_compacts = {norm_key(x) for x in COMMON_TITLE_EXACT_KEYWORDS}
    if compact in bad_compacts:
        return True

    return False


def get_dataset_title_from_list_item(item, fallback=""):
    """
    최종 파일데이터명은 기본적으로 목록 URL 수집 단계의 목록명을 사용합니다.

    다만 목록 카드에서 제목을 정확히 못 잡은 경우가 있어,
    title_source가 anchor가 아니고 상세페이지의 정상 제목이 명확히 다르면 상세 제목으로 보정합니다.
    이 보정은 3075919처럼 다른 URL인데 목록명이 이전 카드명으로 잘못 들어가는 케이스를 막기 위한 안전장치입니다.
    """
    list_title = ""
    for key in ["title", "raw_title"]:
        candidate = clean_dataset_title(item.get(key, ""))
        if candidate and not is_common_title_candidate(candidate):
            list_title = candidate
            break

    fallback_title = strip_dataset_date_suffix(fallback)
    if fallback_title and is_common_title_candidate(fallback_title):
        fallback_title = ""

    title_source = clean_text(item.get("title_source", ""))

    if list_title:
        # URL과 직접 연결된 a 태그에서 얻은 제목이면 목록명을 신뢰합니다.
        if title_source in ["anchor", "detail_anchor"]:
            return list_title

        # span.title 등 보조 선택자로 얻은 제목은 상세 정상 제목과 충돌할 때 상세 제목으로 보정합니다.
        if fallback_title:
            if normalize_title_for_compare(list_title) != normalize_title_for_compare(fallback_title):
                return fallback_title

        return list_title

    return fallback_title

def only_digits(value):
    s = clean_text(value)
    m = re.search(r"[\d,]+", s)
    if not m:
        return s
    return m.group(0).replace(",", "")

def normalize_date(value):
    s = clean_text(value)
    if not s:
        return ""

    m = re.search(r"(20\d{2})[.\-/년\s]+(\d{1,2})[.\-/월\s]+(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"

    return s

def looks_blocked_text(text):
    """
    차단/제한 페이지 여부를 본문 텍스트로 보조 감지합니다.

    v7.2 기준:
    - 데이터 본문/설명에 포함될 수 있는 일반 문자열은 차단으로 보지 않습니다.
      예: 403, 429, 접근 제한, 접근이 제한, Forbidden 단독 표현 등
    - 실제 HTTP 403/429는 status_code에서 이미 판단합니다.
    - 본문 검사는 '명확한 오류 페이지 문맥'일 때만 True를 반환합니다.
    """
    text = clean_text(text)
    if not text:
        return False, ""

    # 너무 긴 정상 상세 본문에서는 단어 하나로 오탐하지 않도록 전체 문장 문맥만 봅니다.
    head = text[:3000]

    # 명확한 오류/차단 페이지 문구만 감지
    phrase_signals = [
        "비정상적인 접근입니다",
        "비정상적인 접근으로 판단",
        "요청이 너무 많습니다",
        "요청이 많아",
        "Too Many Requests",
        "Access Denied",
        "Service Unavailable",
        "서비스 이용이 원활하지 않습니다",
        "자동입력 방지",
        "captcha",
        "CAPTCHA",
    ]

    for sig in phrase_signals:
        if sig in head:
            return True, sig

    # 숫자 403/429 또는 '접근 제한' 단독 문구는 정상 데이터와 충돌할 수 있으므로 제외.
    # HTTP/Error/Status 문맥이 있을 때만 오류 페이지로 판단.
    error_patterns = [
        r"\bHTTP\s*429\b",
        r"\b429\s+Too\s+Many\s+Requests\b",
        r"\bError\s*429\b",
        r"\bHTTP\s*403\b",
        r"\b403\s+Forbidden\b",
        r"\bError\s*403\b",
        r"\bStatus\s*Code\s*[:=]?\s*429\b",
        r"\bStatus\s*Code\s*[:=]?\s*403\b",
        r"페이지\s*접근이\s*제한되었습니다",
        r"접근\s*권한이\s*없습니다",
    ]

    for pat in error_patterns:
        if re.search(pat, head, flags=re.IGNORECASE):
            return True, pat

    return False, ""

async def wait_global_cooldown(block_state):
    """
    429/403 등 rate limit 신호가 감지된 뒤에는 모든 worker가 짧게 쉬도록 합니다.
    평상시에는 바로 통과하므로 전체 속도 저하는 거의 없습니다.
    """
    cooldown_until = block_state.get("cooldown_until", 0.0)
    remain = cooldown_until - time.time()
    if remain > 0:
        print(f"[쿨다운] rate-limit 감지 후 {remain:.1f}초 대기")
        await asyncio.sleep(remain)

async def register_block_signal(block_state, block_lock, signal_text):
    """
    차단/429 신호 발생 시 전체 worker에 공유되는 쿨다운 시간을 갱신합니다.
    """
    async with block_lock:
        block_state["count"] = block_state.get("count", 0) + 1
        count = block_state["count"]

        cooldown = min(
            BLOCK_COOLDOWN_MAX_SEC,
            BLOCK_COOLDOWN_BASE_SEC * min(count, 3)
        )

        if ENABLE_MAIN_COOLDOWN:
            # 429가 여러 worker에서 동시에 터질 수 있으므로 가장 긴 cooldown만 유지
            block_state["cooldown_until"] = max(
                block_state.get("cooldown_until", 0.0),
                time.time() + cooldown
            )
            print(f"[차단 신호] {signal_text} 누적={count}, 전체 쿨다운={cooldown:.1f}초")
        else:
            # 빠른 메인 수집 모드: 차단 신호는 기록하되 전체 worker 쿨다운은 걸지 않음
            block_state["cooldown_until"] = 0.0
            print(f"[차단 신호] {signal_text} 누적={count}, 쿨다운 미적용")

        return count

def is_playwright_driver_closed_error(err_text):
    """
    Playwright 브라우저/드라이버 연결 종료 계열 오류인지 판단합니다.
    이 오류는 403/429 차단 응답이 아니라 브라우저 프로세스/드라이버 연결이 끊긴 상태입니다.
    """
    err_text = str(err_text)
    signals = [
        "Connection closed while reading from the driver",
        "Browser.close: Connection closed",
        "Target page, context or browser has been closed",
        "Browser has been closed",
        "Connection closed",
        "playwright._impl._errors.Error",
    ]
    return any(sig in err_text for sig in signals)

async def safe_close_browser(browser):
    """
    browser.close() 자체가 Connection closed 오류를 내며 전체 실행을 중단하지 않도록 방어합니다.
    """
    if browser is None:
        return
    try:
        await browser.close()
    except Exception as e:
        print(f"[경고] browser.close 중 오류 무시: {repr(e)}")

async def safe_close_context(context):
    """
    context.close() 오류 방어.
    """
    if context is None:
        return
    try:
        await context.close()
    except Exception as e:
        print(f"[경고] context.close 중 오류 무시: {repr(e)}")

async def setup_route(context):
    if not BLOCK_RESOURCE_TYPES:
        return

    blocked_types = {"image", "media", "font", "stylesheet"}

    async def route_handler(route):
        try:
            if route.request.resource_type in blocked_types:
                await route.abort()
            else:
                await route.continue_()
        except Exception:
            try:
                await route.continue_()
            except Exception:
                pass

    await context.route("**/*", route_handler)

async def wait_detail_ready(page):
    try:
        await page.wait_for_function(
            """() => {
                const t = document.body ? document.body.innerText : '';
                return t.includes('파일데이터명') || t.includes('분류체계') || t.includes('제공기관');
            }""",
            timeout=8000,
        )
    except Exception:
        try:
            await page.wait_for_selector("body", timeout=3000)
        except Exception:
            pass

async def wait_list_ready(page):
    try:
        await page.wait_for_selector(
            "div.result-list ul li, a[href*='/data/'][href*='fileData.do']",
            timeout=8000,
        )
    except Exception:
        pass

# ==========================================================
# 3. 목록 URL 수집
# ==========================================================

def extract_value_from_text_by_label(text, label):
    text = clean_text(text)
    labels = [
        "제공기관", "분류체계", "등록일", "수정일",
        "조회수", "조회 수", "다운로드수", "다운로드 수", "다운로드",
        "관리부서명", "관리부서 전화번호", "업데이트 주기",
        "제공형태", "비용부과유무",
    ]

    others = [re.escape(x) for x in labels if x != label]
    if others:
        stop = "|".join(others)
        pattern = rf"{re.escape(label)}\s*[:：]?\s*(.*?)(?=({stop})\s*[:：]?|$)"
    else:
        pattern = rf"{re.escape(label)}\s*[:：]?\s*(.*)$"

    m = re.search(pattern, text)
    if not m:
        return ""

    value = clean_text(m.group(1))
    if len(value) > 200:
        return ""

    return value


def extract_list_count_from_text(text, label_patterns):
    """
    목록 카드 하단의 조회수/다운로드수 값을 추출합니다.
    홈페이지 목록은 보통 "조회수 2930 다운로드 638" 형태로 표시됩니다.
    "조회"처럼 너무 넓은 단어는 오탐 가능성이 있어 사용하지 않습니다.
    """
    text = clean_text(text)
    if not text:
        return ""

    for label in label_patterns:
        label = clean_text(label)
        if not label:
            continue

        compact_label = re.escape(label).replace(r"\ ", r"\s*")
        patterns = [
            rf"{compact_label}\s*[:：]?\s*([0-9][0-9,]*)",
            rf"{compact_label}\s*\(?\s*건\s*\)?\s*[:：]?\s*([0-9][0-9,]*)",
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                return m.group(1).replace(",", "")

    return ""


def extract_list_view_download_counts(text):
    """
    목록 카드 텍스트에서 조회수와 다운로드수를 분리 추출합니다.
    우선 홈페이지 목록 하단의 "조회수 N 다운로드 M" 조합을 직접 찾고,
    실패하면 각각의 라벨 패턴으로 보조 추출합니다.
    """
    text = clean_text(text)
    if not text:
        return "", ""

    pair_patterns = [
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드수?\s*[:：]?\s*([0-9][0-9,]*)",
        r"조회\s*수\s*[:：]?\s*([0-9][0-9,]*)\s+다운로드\s*수\s*[:：]?\s*([0-9][0-9,]*)",
    ]
    for pat in pair_patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return m.group(1).replace(",", ""), m.group(2).replace(",", "")

    view_count = extract_list_count_from_text(
        text,
        ["조회수", "조회 수"],
    )
    download_count = extract_list_count_from_text(
        text,
        ["다운로드수", "다운로드 수", "다운로드"],
    )
    return view_count, download_count

def extract_card_metadata(li, page_url):
    text = clean_text(li.get_text(" "))

    # URL과 목록명이 서로 엇갈리지 않도록, 먼저 상세 URL을 가진 a 태그를 찾고
    # 가능하면 같은 a 태그의 텍스트를 목록명으로 사용합니다.
    detail_anchor = None
    full_url = ""
    for a in li.select("a[href*='/data/'], a[href*='/dataset/']"):
        href = a.get("href", "") if a else ""
        candidate_url = absolute_url(page_url, href)
        if is_detail_url(candidate_url):
            detail_anchor = a
            full_url = candidate_url
            break

    raw_title = ""
    title_source = ""

    if detail_anchor is not None:
        anchor_text = clean_dataset_title(detail_anchor.get_text(" "))
        if anchor_text and not is_common_title_candidate(anchor_text):
            raw_title = anchor_text
            title_source = "anchor"

    if not raw_title:
        title_el = li.select_one("span.title") or li.select_one(".title")
        if title_el:
            candidate = clean_dataset_title(title_el.get_text(" "))
            if candidate and not is_common_title_candidate(candidate):
                raw_title = candidate
                title_source = "title_selector"

    if not raw_title and detail_anchor is not None:
        raw_title = clean_text(detail_anchor.get_text(" "))
        title_source = "anchor_raw"

    formats = detect_formats(raw_title or text)
    view_count, download_count = extract_list_view_download_counts(text)

    item_meta = {
        "raw_title": raw_title,
        "title": clean_dataset_title(raw_title),
        "title_source": title_source,
        "확장자": " ".join(formats),
        "조회수": view_count,
        "다운로드(바로가기)": download_count,
        "다운로드수": download_count,
        "detail_url": full_url,
        "source_list_url": page_url,
    }

    for target in ["제공기관", "분류체계", "등록일", "수정일"]:
        value = extract_value_from_text_by_label(text, target)
        if value:
            item_meta[target] = value

    return item_meta

def collect_dataset_links_from_html(html, page_url):
    soup = BeautifulSoup(html, "lxml")
    items = []
    seen = set()

    for li in soup.select("div.result-list ul li"):
        item = extract_card_metadata(li, page_url)
        full_url = item.get("detail_url", "")

        if not is_detail_url(full_url):
            continue

        if full_url not in seen:
            seen.add(full_url)
            items.append(item)

    if not items:
        for a in soup.select("a[href*='/data/'], a[href*='/dataset/']"):
            href = a.get("href", "")
            full_url = absolute_url(page_url, href)

            if not is_detail_url(full_url):
                continue

            raw_title = clean_text(a.get_text(" "))
            item = {
                "raw_title": raw_title,
                "title": clean_dataset_title(raw_title),
                "title_source": "fallback_anchor",
                "확장자": " ".join(detect_formats(raw_title)),
                "조회수": "",
                "다운로드(바로가기)": "",
                "다운로드수": "",
                "detail_url": full_url,
                "source_list_url": page_url,
            }

            if full_url not in seen:
                seen.add(full_url)
                items.append(item)

    return items

async def collect_list_items(browser, target_url, max_pages, max_detail_items, list_per_page):
    context = await browser.new_context(
        locale="ko-KR",
        viewport={"width": 1400, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        extra_http_headers={
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        },
    )
    await setup_route(context)
    page = await context.new_page()

    all_items = []
    seen = set()

    try:
        if is_detail_url(target_url):
            return [{
                "raw_title": "",
                "title": "",
                "title_source": "direct_url",
                "확장자": "",
                "조회수": "",
                "다운로드(바로가기)": "",
                "다운로드수": "",
                "detail_url": target_url,
                "source_list_url": "",
            }]

        page_no = 1
        while True:
            if max_pages > 0 and page_no > max_pages:
                break

            list_url = optimize_list_url(target_url, list_per_page, current_page=page_no)
            print(f"[LIST] page {page_no:02d}/{max_pages if max_pages > 0 else 0} 수집 중")

            await page.goto(list_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await wait_list_ready(page)
            await asyncio.sleep(random.uniform(PAGE_JITTER_MIN_SEC, PAGE_JITTER_MAX_SEC))

            html = await page.content()
            items = collect_dataset_links_from_html(html, page.url)

            print(f"[LIST] page {page_no:02d} +{len(items):>3}건 | 누적 {len(all_items):,}/{max_detail_items:,}")

            for item in items:
                url = item.get("detail_url", "")
                if url and url not in seen:
                    seen.add(url)
                    all_items.append(item)

                    if max_detail_items > 0 and len(all_items) >= max_detail_items:
                        return all_items[:max_detail_items]

            if not items:
                break

            page_no += 1

    finally:
        await safe_close_context(context)

    return all_items[:max_detail_items] if max_detail_items > 0 else all_items

# ==========================================================
# 4. 상세 파싱 로직
# ==========================================================

def extract_phone_number(value):
    """
    관리부서 전화번호 값에서 실제 전화번호 패턴을 추출합니다.
    공공데이터포털 상세페이지는 053-670-0619, 02-1234-5678,
    1577-0000, 0536700619, 02)1234-5678, 000-0000-0000 등
    표기 방식이 섞여 있어 하이픈 유무와 괄호/공백을 함께 허용합니다.
    """
    text = clean_text(value)
    if not text:
        return ""

    patterns = [
        r"(?:\+?82[-\s]?)?0\d{1,2}\s*[\)\-\.]?\s*[0-9xX*]{3,4}\s*[\-\.]?\s*[0-9xX*]{4}",
        r"(?:\+?82[-\s]?)?10\s*[\-\.]?\s*[0-9xX*]{4}\s*[\-\.]?\s*[0-9xX*]{4}",
        r"1[0-9xX*]{3}\s*[\-\.]?\s*[0-9xX*]{4}",
        r"\d{2,4}\s*[\-\.]\s*[0-9xX*]{3,4}\s*[\-\.]\s*[0-9xX*]{4}",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            phone = clean_text(m.group(0))
            phone = re.sub(r"\s+", "", phone)
            phone = phone.replace(".", "-").replace(")", "-")
            phone = re.sub(r"-+", "-", phone).strip("-")
            return phone

    return ""



def is_portal_support_phone_value(value):
    """
    오픈API 자동변환 영역/포털 푸터의 공공데이터활용지원센터 전화번호가
    파일데이터 관리부서 전화번호로 섞이는 것을 방지합니다.
    """
    text = clean_text(value)
    if not text:
        return False
    phone = extract_phone_number(text)
    portal_terms = [
        "공공데이터활용지원센터",
        "공공데이터 개방문의",
        "대표번호",
        "관리기관",
        "오픈API",
        "오픈 API",
    ]
    return phone == "1566-0025" and any(term in text for term in portal_terms)


def extract_phone_from_element(el):
    """
    DOM 요소 내부에서 전화번호를 추출합니다.

    공공데이터포털 일부 상세페이지는 화면에는
    <span id="telNo1">02-2127-5601</span>처럼 보이지만,
    httpx로 받은 원본 HTML에서는 span 텍스트가 비어 있고
    바로 뒤 script에서 telNo 값을 세팅하는 경우가 있습니다.

    BeautifulSoup의 get_text()는 script 내용을 일반 텍스트로 포함하지 않는 경우가 있어,
    1) 일반 표시 텍스트
    2) 하위 script 문자열
    3) 하위 태그의 주요 속성값
    을 함께 검사합니다.
    """
    if el is None:
        return ""

    candidates = []

    try:
        text_value = clean_text(el.get_text(" "))
        if text_value:
            candidates.append(text_value)
    except Exception:
        pass

    # script 내부에 $("#telNo1").text("02-2127-5601") 같은 형태로 들어간 값 대응
    try:
        for script in el.find_all("script"):
            script_text = ""
            if script.string:
                script_text = str(script.string)
            else:
                script_text = script.get_text(" ")
            script_text = clean_text(script_text)
            if script_text:
                candidates.append(script_text)
    except Exception:
        pass

    # span/input 등에 data-value, value, title 등으로 들어간 케이스 대응
    try:
        for tag in el.find_all(True):
            for attr in ["value", "data-value", "data-tel", "data-phone", "title", "aria-label", "onclick"]:
                attr_value = tag.get(attr)
                if attr_value:
                    candidates.append(clean_text(attr_value))
    except Exception:
        pass

    joined = " ".join(candidates)
    if is_portal_support_phone_value(joined):
        return ""

    for candidate in candidates:
        phone = extract_phone_number(candidate)
        if phone:
            return phone

    return ""


PHONE_LABEL_VARIANTS = [
    "관리부서 전화번호",
    "관리부서 전화 번호",
    "담당부서 전화번호",
    "담당부서 전화 번호",
]
PHONE_LABEL_NORMS = {norm_key(x) for x in PHONE_LABEL_VARIANTS}
PHONE_LABEL_REGEX = r"관리\s*부서\s*전화\s*번호|담당\s*부서\s*전화\s*번호"


def is_metadata_label_text(value):
    """
    전화번호 주변 DOM 탐색 중 다음 메타데이터 라벨을 만나면 값 탐색을 멈추기 위한 보조 함수입니다.
    """
    nk = norm_key(value)
    if not nk:
        return False
    if nk in PHONE_LABEL_NORMS:
        return True
    for label in METADATA_LABEL_SEQUENCE:
        if nk == norm_key(label):
            return True
    return False


def extract_value_after_phone_label_from_text(text):
    """
    평문 블록에서 '관리부서 전화 번호 053-670-0619' 형태를 직접 추출합니다.
    """
    text = clean_text(text)
    if not text:
        return ""

    stop_labels = [
        "관리부서명", "보유근거", "수집방법", "업데이트 주기", "차기 등록 예정일",
        "매체유형", "전체 행", "확장자", "키워드", "데이터 한계", "조회수",
        "다운로드(바로가기)", "등록일", "수정일", "제공형태", "설명", "기타 유의사항",
        "공간범위", "시간범위", "비용부과유무", "비용부과기준 및 단위", "이용허락범위",
    ]
    stop = "|".join(re.escape(x) for x in sorted(stop_labels, key=len, reverse=True))

    m = re.search(
        rf"(?:{PHONE_LABEL_REGEX})\s*[:：]?\s*(.*?)(?=({stop})\s*[:：]?|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return ""

    value = clean_text(m.group(1))
    phone = extract_phone_number(value)
    return phone or value


def extract_management_phone_number_from_soup(soup):
    """
    상세페이지의 '관리부서 전화 번호' 값을 보강 추출합니다.
    출력 컬럼은 항상 '관리부서 전화번호' 하나로만 저장하고,
    상세페이지의 변형 라벨(관리부서 전화 번호/담당부서 전화번호)은
    같은 값으로 통합합니다.

    핵심 보정:
    - httpx는 JS 실행 전 원본 HTML만 받기 때문에 telNo span이 비어 있을 수 있습니다.
    - 따라서 th/dt 옆 td/dd 내부의 script 문자열까지 검사해 전화번호를 추출합니다.
    - 관리부서 전화번호 라벨이 실제로 존재하지만 값이 빈 경우에는
      오픈API 영역의 1566-0025로 넘어가지 않도록 즉시 빈값 처리합니다.
    """

    # 1) th-td / dt-dd 구조 우선 확인
    for label_el in soup.find_all(["th", "dt"]):
        label_text = clean_text(label_el.get_text(" "))
        label_norm = norm_key(label_text)
        if label_norm not in PHONE_LABEL_NORMS:
            continue

        value_el = label_el.find_next_sibling(["td", "dd"])
        if value_el is not None:
            value = clean_text(value_el.get_text(" "))
            phone = extract_phone_from_element(value_el) or extract_phone_number(value)

            if phone:
                return phone

            # 파일데이터 정보 영역에 관리부서 전화번호 라벨이 있는데 값이 빈 경우.
            # 이 상태에서 계속 전역 탐색하면 오픈API/푸터 전화번호가 섞일 수 있으므로 여기서 종료합니다.
            if not value:
                return ""

            if is_portal_support_phone_value(value):
                return ""

            return value

    # 2) span/div/strong 등 임의 태그에 라벨이 들어간 구조 처리
    for label_el in soup.find_all(True):
        label_text = clean_text(label_el.get_text(" "))
        label_norm = norm_key(label_text)

        # 라벨 단독 태그만 대상으로 삼습니다. 값까지 포함된 부모 태그는 아래 텍스트 블록에서 처리합니다.
        if label_norm not in PHONE_LABEL_NORMS:
            continue

        # 2-1) 같은 부모 안에서 라벨 뒤쪽 형제들을 확인
        parent = label_el.parent
        if parent is not None:
            seen_label = False
            parts = []
            for child in parent.find_all(recursive=False):
                if child is label_el:
                    seen_label = True
                    continue
                if not seen_label:
                    continue

                child_phone = extract_phone_from_element(child)
                if child_phone:
                    return child_phone

                child_text = clean_text(child.get_text(" "))
                if not child_text:
                    continue
                if is_metadata_label_text(child_text):
                    break
                parts.append(child_text)
                if extract_phone_number(" ".join(parts)):
                    break

            value = clean_text(" ".join(parts))
            if value:
                if is_portal_support_phone_value(value):
                    return ""
                phone = extract_phone_number(value)
                return phone or value

        # 2-2) 문서 순서상 다음 몇 개 태그 확인
        parts = []
        next_el = label_el
        for _ in range(8):
            next_el = next_el.find_next()
            if next_el is None:
                break
            if getattr(next_el, "name", None) is None:
                continue

            phone_from_el = extract_phone_from_element(next_el)
            if phone_from_el:
                return phone_from_el

            value = clean_text(next_el.get_text(" "))
            if not value or value == label_text:
                continue
            if is_metadata_label_text(value):
                break
            parts.append(value)
            candidate_text = " ".join(parts)
            if is_portal_support_phone_value(candidate_text):
                return ""
            phone = extract_phone_number(candidate_text)
            if phone:
                return phone

    # 3) 파일데이터 메타 블록 텍스트에서 직접 추출
    block = extract_file_metadata_text_block(soup)
    if block:
        value = extract_value_after_phone_label_from_text(block)
        if value and not is_portal_support_phone_value(value):
            return value

    # 4) 전체 페이지 텍스트 최후 보조는 사용하지 않습니다.
    #    공공데이터포털 페이지에는 오픈API 영역의
    #    "관리기관 공공데이터활용지원센터 / 관리기관 전화번호 1566-0025"와
    #    하단 대표번호가 함께 존재합니다.
    #    이 값을 파일데이터의 관리부서 전화번호로 오인하지 않도록
    #    파일데이터 메타 블록 안에서만 전화번호를 찾습니다.
    return ""

def is_column_table(table):
    """
    공공데이터포털의 실제 '데이터항목(컬럼) 정보' 표만 True로 판단합니다.

    기존 방식은 상세 HTML 전체 table에서 '항목', '설명', '타입' 같은 넓은 키워드만 보고
    파일데이터 정보/오픈API 정보 표까지 컬럼정보로 오인할 수 있었습니다.
    여기서는 헤더 행에 '항목명'과 '데이터타입'이 동시에 있는 표만 컬럼정보로 인정합니다.
    """
    return find_column_header_info(table) is not None

def set_target_value(metadata, raw_key, raw_value, source="table"):
    key = clean_text(raw_key)
    value = clean_text(raw_value)

    if not key or not value:
        return

    nk = norm_key(key)
    target = LABEL_TO_TARGET.get(key) or LABEL_TO_TARGET.get(nk)

    if not target or target not in metadata:
        return

    current = clean_text(metadata.get(target, ""))

    if target == "관리부서 전화번호":
        # 오픈API 영역의 관리기관 전화번호/포털 대표번호가 섞인 값은 버립니다.
        if is_portal_support_phone_value(value):
            return
        phone = extract_phone_number(value)
        if phone:
            metadata[target] = phone
        elif not current:
            metadata[target] = value
        return

    if target == "파일데이터명":
        # 파일데이터명은 목록 URL 수집 단계의 목록명을 우선 사용합니다.
        # 상세페이지의 제목/테이블 값으로 덮어쓰지 않습니다.
        if not current:
            metadata[target] = value
        return

    if target == "다운로드(바로가기)":
        # 다운로드수는 상세페이지의 누적 다운로드 값이 아니라 목록 카드에서 가져온 값을 사용합니다.
        return

    if target == "확장자":
        if source in ["table", "text_fallback"] or not current:
            metadata[target] = value.replace(",", " ").strip()
        return

    if not current:
        metadata[target] = value
    elif source in ["table", "text_fallback"]:
        polluted_values = {
            "수집방법", "다운로드(바로가기)", "시간범위", "이용허락범위",
            "보유근거", "데이터 한계", "기타 유의사항",
        }
        if current in polluted_values:
            metadata[target] = value

def extract_metadata_pairs_from_tables(soup):
    pairs = []

    for table in soup.select("table"):
        if is_column_table(table):
            continue

        for tr in table.select("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                cells = tr.find_all(["th", "td"])
            if len(cells) < 2:
                continue

            extracted_in_row = False
            for i, cell in enumerate(cells[:-1]):
                if cell.name.lower() != "th":
                    continue

                key = clean_text(cell.get_text(" "))
                if not key:
                    continue

                next_cell = cells[i + 1]
                if next_cell.name.lower() != "td":
                    continue

                value = clean_text(next_cell.get_text(" "))
                # 관리부서 전화번호는 원본 HTML에서 span 텍스트가 비어 있고
                # script로 telNo 값을 세팅하는 경우가 있어 요소 내부 script까지 확인합니다.
                if norm_key(key) in PHONE_LABEL_NORMS and not extract_phone_number(value):
                    phone_from_element = extract_phone_from_element(next_cell)
                    if phone_from_element:
                        value = phone_from_element
                if len(key) <= 80:
                    pairs.append((key, value))
                    extracted_in_row = True

            if not extracted_in_row:
                texts = [clean_text(c.get_text(" ")) for c in cells]
                for i in range(0, len(texts) - 1, 2):
                    key = texts[i]
                    value = texts[i + 1]
                    if key and len(key) <= 80:
                        pairs.append((key, value))

    for dl in soup.select("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            key = clean_text(dt.get_text(" "))
            value = clean_text(dd.get_text(" "))
            if key:
                pairs.append((key, value))

    return pairs

def extract_title_from_detail(soup, fallback=""):
    candidates = []

    selectors = [
        ".data-title",
        ".title",
        ".tit",
        ".view-title",
        ".dataset-title",
        "h1",
        "h2",
        "h3",
        "title",
    ]

    for selector in selectors:
        for el in soup.select(selector):
            text = clean_text(el.get_text(" "))
            if text:
                candidates.append(text)

    for text in candidates:
        cleaned = clean_dataset_title(text)
        if (
            cleaned
            and cleaned not in ["데이터 상세", "파일데이터", "상세", "공공데이터포털"]
            and not is_common_title_candidate(cleaned)
            and ("_" in cleaned or len(cleaned) > 10)
        ):
            return cleaned

    fallback_title = clean_dataset_title(fallback)
    if fallback_title and not is_common_title_candidate(fallback_title):
        return fallback_title

    return fallback_title

def extract_download_count(soup):
    text = clean_text(soup.get_text(" "))
    patterns = [
        r"다운로드\s*\(?바로가기\)?\s*([0-9,]+)",
        r"다운로드수\s*([0-9,]+)",
        r"다운로드\s*([0-9,]+)",
    ]

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).replace(",", "")

    return ""

METADATA_LABEL_SEQUENCE = [
    "파일데이터명",
    "분류체계",
    "제공기관",
    "관리부서명",
    "관리부서 전화번호",
    "관리부서 전화 번호",
    "담당부서 전화번호",
    "보유근거",
    "수집방법",
    "업데이트 주기",
    "차기 등록 예정일",
    "매체유형",
    "전체 행",
    "확장자",
    "키워드",
    "데이터 한계",
    "조회수",
    "다운로드(바로가기)",
    "등록일",
    "수정일",
    "제공형태",
    "설명",
    "기타 유의사항",
    "공간범위",
    "시간범위",
    "비용부과유무",
    "비용부과기준 및 단위",
    "이용허락범위",
]

def extract_file_metadata_text_block(soup):
    """
    정상 파일데이터 상세 메타데이터 블록을 찾습니다.

    기존 로직은 전체 텍스트를 먼저 '오픈API 정보' 기준으로 잘랐는데,
    페이지 구조에 따라 파일데이터 영역 앞쪽의 안내/탭 문구와 섞이면서 정상 상세페이지도
    메타 블록을 못 찾는 경우가 있었습니다.

    수정 방향:
    1) 전체 텍스트에서 '파일데이터명' 또는 '파일데이터 정보'가 등장하는 모든 위치를 후보로 잡습니다.
    2) 각 후보 위치에서 일정 구간만 잘라 검사합니다.
    3) 그 후보 블록 내부에서 뒤쪽에 '오픈API 정보'가 붙는 경우에만 뒤를 잘라냅니다.
    4) 분류체계 + 제공기관 조합과 라벨 점수로 실제 파일데이터 메타 블록을 선택합니다.
    """
    text = clean_text(soup.get_text(" "))
    if not text:
        return ""

    starts = []
    for marker in ["파일데이터명", "파일데이터 정보"]:
        starts.extend(m.start() for m in re.finditer(re.escape(marker), text))

    starts = sorted(set(starts))
    if not starts:
        return ""

    best_block = ""
    best_score = -1

    for start in starts:
        block = text[start:start + 8000]

        # 후보 블록 뒤쪽에 오픈API 영역이 붙은 경우에만 절단합니다.
        # 전체 텍스트를 먼저 자르지 않습니다.
        if "오픈API 정보" in block:
            block = block.split("오픈API 정보", 1)[0]

        if "분류체계" not in block or "제공기관" not in block:
            continue

        # 파일데이터 상세 블록은 보통 이 중 여러 라벨을 포함합니다.
        score = sum(1 for label in METADATA_LABEL_SEQUENCE if label in block)

        # 실제 메타데이터 값이 시작되는 '파일데이터명' 위치를 가장 우선합니다.
        if text[start:start + 20].find("파일데이터명") >= 0:
            score += 20

        # API 상세블록보다 파일데이터 블록을 우선합니다.
        if "파일데이터명" in block:
            score += 5
        if "서비스" in block and "파일데이터명" not in block:
            score -= 5

        if score > best_score:
            best_score = score
            best_block = block

    return best_block

def extract_metadata_pairs_from_text_block(soup):
    block = extract_file_metadata_text_block(soup)
    if not block:
        return []

    labels = METADATA_LABEL_SEQUENCE
    label_pattern = "|".join(re.escape(x) for x in sorted(labels, key=len, reverse=True))

    matches = list(re.finditer(label_pattern, block))
    if not matches:
        return []

    pairs = []
    for idx, m in enumerate(matches):
        label = m.group(0)
        value_start = m.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(block)
        value = clean_text(block[value_start:value_end])

        for stop_word in [
            "공공데이터활용지원센터는",
            "오픈 API를 활용하기 위해서는",
            "XML JSON",
            "활용신청",
            "오픈API 정보",
            "다른 사용자들이 활용한 데이터",
        ]:
            if stop_word in value:
                value = clean_text(value.split(stop_word, 1)[0])

        pairs.append((label, value))

    return pairs


def detect_invalid_filedata_detail_page(soup):
    """
    상세 URL이 실제 파일데이터 상세페이지가 아니라
    데이터목록/공통/오류 안내 화면을 반환한 경우만 감지합니다.

    중요:
    - 정상 상세페이지의 설명/기타 유의사항/데이터 한계/항목설명 안에 들어간
      "존재하지 않는 데이터", "폐기", "서비스 중지" 같은 문구로는 폐기 여부를 판단하지 않습니다.
    - 먼저 정상 파일데이터 메타데이터 블록이 있는지 확인하고, 정상 블록이 있으면 무조건 정상 처리합니다.
    - 정상 블록이 없을 때만 목록/검색 화면 또는 페이지 자체 오류 화면 여부를 판단합니다.
    """
    text = clean_text(soup.get_text(" "))
    if not text:
        return "EMPTY_BODY"

    # 1) 정상 파일데이터 상세 블록이 있으면 설명/유의사항 문구와 관계없이 정상 페이지로 판단합니다.
    #    이 블록은 파일데이터명 + 분류체계 + 제공기관 조합을 기준으로 찾습니다.
    if extract_file_metadata_text_block(soup):
        return ""

    head = text[:8000]

    # 2) 공공데이터포털 데이터목록/검색 화면으로 떨어진 경우만 비정상 상세페이지로 판단합니다.
    if (
        ("어떤 공공데이터를 찾으시나요" in head or "어떤 공공데이터를 찾으시나요?" in head)
        and ("조건검색" in head or "건이 검색되었습니다" in head)
    ):
        return "DATASET_LIST_PAGE"

    if "데이터목록" in head and "건이 검색되었습니다" in head and "조건검색" in head:
        return "DATASET_LIST_PAGE"

    # 3) 페이지 자체가 오류/접근불가 안내 화면인 경우만 비정상으로 판단합니다.
    #    데이터 설명·유의사항에 들어갈 수 있는 일반 문구는 포함하지 않습니다.
    page_error_signals = [
        "요청하신 페이지를 찾을 수 없습니다",
        "페이지를 찾을 수 없습니다",
        "존재하지 않는 페이지",
        "잘못된 접근입니다",
        "비정상적인 접근입니다",
        "비정상적인 접근으로 판단",
        "접근 권한이 없습니다",
        "페이지 접근이 제한되었습니다",
    ]
    for signal in page_error_signals:
        if signal in head:
            return f"UNAVAILABLE_PAGE:{signal}"

    return ""


def make_invalid_detail_metadata(item, detail_url, final_seq, source_file_label, invalid_reason):
    """
    폐기/비정상 상세 URL도 메타데이터 파일에 남기기 위한 최소 메타데이터 행을 만듭니다.
    파일데이터명은 상세 공통화면 제목이 아니라 목록 수집 단계의 item title을 우선 사용합니다.
    """
    metadata = {col: "" for col in TARGET_METADATA_COLUMNS}

    dataset_name = clean_dataset_title(item.get("title", ""))
    if not dataset_name or is_common_title_candidate(dataset_name):
        dataset_name = clean_dataset_title(item.get("raw_title", ""))

    if not dataset_name or is_common_title_candidate(dataset_name):
        m = re.search(r"/(?:data|dataset)/(\d+)", clean_text(detail_url))
        dataset_id = m.group(1) if m else str(final_seq)
        dataset_name = f"상세페이지 확인필요_{dataset_id}"

    metadata["최종순번"] = final_seq
    metadata["파일데이터명"] = dataset_name
    metadata["상세페이지 URL"] = detail_url
    metadata["확장자"] = item.get("확장자", "")
    metadata["조회수"] = item.get("조회수", "")
    metadata["다운로드(바로가기)"] = item.get("다운로드(바로가기)", "") or item.get("다운로드수", "")
    metadata["수집파일"] = source_file_label or "실시간수집"

    for k in ["제공기관", "분류체계", "등록일", "수정일"]:
        if item.get(k):
            metadata[k] = item[k]

    for col in ["등록일", "수정일"]:
        metadata[col] = normalize_date(metadata.get(col, ""))

    metadata["데이터 한계"] = (
        "상세페이지가 정상 파일데이터 상세화면이 아니어서 "
        f"목록 수집값 기준으로 저장했습니다. [{invalid_reason}]"
    )

    return metadata

def parse_metadata_target(soup, item, detail_url, final_seq, source_file_label):
    metadata = {col: "" for col in TARGET_METADATA_COLUMNS}

    detail_title = extract_title_from_detail(soup, item.get("title", ""))
    title = get_dataset_title_from_list_item(item, fallback=detail_title)

    metadata["파일데이터명"] = title
    metadata["상세페이지 URL"] = detail_url
    metadata["확장자"] = item.get("확장자", "")
    metadata["조회수"] = item.get("조회수", "")
    metadata["다운로드(바로가기)"] = item.get("다운로드(바로가기)", "") or item.get("다운로드수", "")
    metadata["수집파일"] = source_file_label or "실시간수집"
    metadata["최종순번"] = final_seq

    for k in ["제공기관", "분류체계", "등록일", "수정일"]:
        if item.get(k):
            metadata[k] = item[k]

    for key, value in extract_metadata_pairs_from_tables(soup):
        set_target_value(metadata, key, value, source="table")

    for key, value in extract_metadata_pairs_from_text_block(soup):
        set_target_value(metadata, key, value, source="text_fallback")

    # 일부 상세페이지에서 관리부서 전화번호가 표/텍스트 구조상 일반 pair 추출에 잡히지 않는 경우가 있어 보강합니다.
    current_phone = clean_text(metadata.get("관리부서 전화번호", ""))
    if not extract_phone_number(current_phone):
        phone_value = extract_management_phone_number_from_soup(soup)
        if phone_value:
            metadata["관리부서 전화번호"] = phone_value

    for col in ["등록일", "수정일", "차기 등록 예정일"]:
        metadata[col] = normalize_date(metadata.get(col, ""))

    if not metadata["확장자"]:
        metadata["확장자"] = " ".join(detect_formats(soup.get_text(" ")))

    if metadata["전체 행"]:
        metadata["전체 행"] = only_digits(metadata["전체 행"])

    return metadata

COLUMN_SECTION_TITLE_PATTERNS = [
    "데이터항목(컬럼) 정보",
    "데이터 항목(컬럼) 정보",
    "데이터항목 정보",
    "데이터 항목 정보",
]

COLUMN_SKIP_LABELS = {
    norm_key(x) for x in [
        "정보시스템명", "DB명", "Table명", "테이블명",
        "파일데이터명", "분류체계", "제공기관", "관리부서명", "관리부서 전화번호",
        "보유근거", "수집방법", "업데이트 주기", "차기 등록 예정일", "매체유형",
        "전체 행", "확장자", "키워드", "데이터 한계", "다운로드(바로가기)",
        "등록일", "수정일", "제공형태", "설명", "기타 유의사항", "공간범위",
        "시간범위", "비용부과유무", "비용부과기준 및 단위", "이용허락범위",
        "서비스", "관리기관", "관리기관 전화번호", "활용신청",
    ]
}

HEADER_NAME_KEYS = {"항목명", "컬럼명", "필드명"}
HEADER_DESC_KEYS = {"항목설명", "컬럼설명", "설명"}
HEADER_TYPE_KEYS = {"데이터타입", "타입"}
HEADER_LENGTH_KEYS = {"최대길이", "최대길이", "데이터길이", "길이"}


def get_row_cells_text(tr):
    cells = tr.find_all(["th", "td"], recursive=False)
    if not cells:
        cells = tr.find_all(["th", "td"])
    return [clean_text(c.get_text(" ")) for c in cells]


def find_first_index_by_norm(headers, allowed_norm_keys):
    for i, header in enumerate(headers):
        if norm_key(header) in allowed_norm_keys:
            return i
    return None


def find_column_header_info(table):
    """
    컬럼정보 표의 헤더 위치와 필요한 컬럼 index를 찾습니다.
    공공데이터포털 컬럼정보 표는 보통 다음 헤더를 가집니다.
    - 항목명 / 항목 설명 / 데이터타입 / 최대길이
    - 생성출처 하위 헤더(정보시스템명, DB명, Table명)는 두 번째 헤더행으로 붙을 수 있어 제외합니다.
    """
    trs = table.select("tr")
    for row_idx, tr in enumerate(trs[:4]):
        headers = get_row_cells_text(tr)
        if not headers:
            continue

        compact_headers = [norm_key(h) for h in headers]
        idx_name = find_first_index_by_norm(headers, HEADER_NAME_KEYS)
        idx_desc = find_first_index_by_norm(headers, HEADER_DESC_KEYS)
        idx_type = find_first_index_by_norm(headers, HEADER_TYPE_KEYS)
        idx_len = find_first_index_by_norm(headers, HEADER_LENGTH_KEYS)

        # 실제 컬럼정보 표는 최소한 항목명과 데이터타입이 헤더에 같이 있습니다.
        if idx_name is not None and idx_type is not None:
            return {
                "header_row_idx": row_idx,
                "headers": headers,
                "idx_name": idx_name,
                "idx_desc": idx_desc,
                "idx_type": idx_type,
                "idx_len": idx_len,
            }

        # 일부 표는 데이터타입 대신 최대길이까지만 노출될 수 있어 보조 허용.
        # 단, 항목 설명 또는 길이 중 하나는 있어야 메타데이터 표 오인을 줄입니다.
        if idx_name is not None and (idx_desc is not None or idx_len is not None):
            if any(k in compact_headers for k in ["항목설명", "컬럼설명", "최대길이", "데이터길이"]):
                return {
                    "header_row_idx": row_idx,
                    "headers": headers,
                    "idx_name": idx_name,
                    "idx_desc": idx_desc,
                    "idx_type": idx_type,
                    "idx_len": idx_len,
                }

    return None


def is_column_subheader_or_meta_row(cells):
    if not cells:
        return True

    compact_values = [norm_key(v) for v in cells if clean_text(v)]
    if not compact_values:
        return True

    # 생성출처 하위 헤더 또는 메타데이터 라벨 행 제거
    if all(v in COLUMN_SKIP_LABELS for v in compact_values):
        return True

    joined = "".join(compact_values)
    if "정보시스템명DB명Table명" in joined or "정보시스템명DB명테이블명" in joined:
        return True

    # header row 재등장 제거
    if "항목명" in compact_values and ("데이터타입" in compact_values or "항목설명" in compact_values):
        return True

    return False


def get_cell_by_idx(cells, idx):
    if idx is None:
        return ""
    if idx < 0 or idx >= len(cells):
        return ""
    return clean_text(cells[idx])


def parse_html_table_records(table):
    """
    실제 컬럼정보 표에서 필요한 4개 값만 추출합니다.
    반환 필드는 항목명, 항목설명, 데이터타입, 데이터 길이입니다.
    """
    info = find_column_header_info(table)
    if not info:
        return []

    rows = []
    trs = table.select("tr")
    for tr in trs[info["header_row_idx"] + 1:]:
        cells = get_row_cells_text(tr)
        if is_column_subheader_or_meta_row(cells):
            continue

        item_name = get_cell_by_idx(cells, info["idx_name"])
        item_desc = get_cell_by_idx(cells, info["idx_desc"])
        data_type = get_cell_by_idx(cells, info["idx_type"])
        data_len = get_cell_by_idx(cells, info["idx_len"])

        # 항목명이 메타데이터 라벨이면 컬럼정보가 아닙니다.
        if not item_name or norm_key(item_name) in COLUMN_SKIP_LABELS:
            continue

        # 파일데이터 정보 표 오인을 방지하기 위해 실제 컬럼정보 성격의 값만 남깁니다.
        # 데이터타입/길이/설명 중 하나도 없으면 컬럼행으로 보기 어렵습니다.
        if not any([item_desc, data_type, data_len]):
            continue

        rows.append({
            "항목명": item_name,
            "항목설명": item_desc,
            "데이터타입": data_type,
            "데이터 길이": data_len,
        })

    return rows


def get_record_value_by_std_key(record, std_col):
    value = record.get(std_col, "")
    if clean_text(value):
        return clean_text(value)

    for raw_key, raw_value in record.items():
        if not clean_text(raw_value):
            continue
        nk = norm_key(raw_key)
        mapped = COLUMN_LABEL_MAP.get(raw_key) or COLUMN_LABEL_MAP.get(nk)
        if mapped == std_col:
            return clean_text(raw_value)
    return ""


def is_header_like_column_row(out):
    item_name = clean_text(out.get("항목명", ""))
    if not item_name:
        return True

    compact_name = norm_key(item_name)
    if compact_name in COLUMN_SKIP_LABELS:
        return True

    joined = " ".join(clean_text(v) for v in out.values())
    compact = norm_key(joined)

    header_signals = [
        "정보시스템명DB명Table명",
        "정보시스템명DB명테이블명",
        "정보시스템명데이터베이스명Table명",
    ]
    return any(sig in compact for sig in header_signals)


def parse_columns_target(soup, dataset_name, detail_url):
    """
    '데이터항목(컬럼) 정보'가 있는 페이지에서만 컬럼정보를 수집합니다.
    수집 필드는 항목명, 항목설명, 데이터타입, 데이터 길이로 제한합니다.
    """
    column_rows = []
    seq = 1

    for table in soup.select("table"):
        if not is_column_table(table):
            continue

        records = parse_html_table_records(table)
        for rec in records:
            out = {
                "파일데이터명": dataset_name,
                "상세페이지 URL": detail_url,
                "순번": seq,
                "항목명": get_record_value_by_std_key(rec, "항목명"),
                "항목설명": get_record_value_by_std_key(rec, "항목설명"),
                "데이터타입": get_record_value_by_std_key(rec, "데이터타입"),
                "데이터 길이": get_record_value_by_std_key(rec, "데이터 길이"),
            }

            if is_header_like_column_row(out):
                continue

            column_rows.append(out)
            seq += 1

    return column_rows


def make_column_list(column_rows):
    names = []
    for row in column_rows:
        name = clean_text(row.get("항목명", ""))
        if name and name not in names:
            names.append(name)
    return ", ".join(names)

def make_empty_column_placeholder(dataset_name):
    """
    데이터항목(컬럼) 정보가 없는 파일데이터도 컬럼정보.xlsx에서 누락되지 않도록
    파일데이터명만 채운 1행을 생성합니다.
    사용자가 요청한 기준에 맞춰 상세페이지 URL, 순번, 항목명, 항목설명,
    데이터타입, 데이터 길이는 모두 빈칸으로 둡니다.
    """
    return {
        "파일데이터명": dataset_name,
        "상세페이지 URL": "",
        "순번": "",
        "항목명": "",
        "항목설명": "",
        "데이터타입": "",
        "데이터 길이": "",
    }

def parse_detail_html(html, item, final_seq, source_file_label):
    soup = BeautifulSoup(html, "lxml")
    detail_url = item["detail_url"]

    # 폐기/비정상 상세 URL이 데이터목록/공통 페이지를 반환하는 경우,
    # 상단 탭/메뉴명을 파일데이터명으로 오인하지 않고 목록 수집값 기준으로 저장합니다.
    invalid_reason = detect_invalid_filedata_detail_page(soup)
    if invalid_reason:
        metadata = make_invalid_detail_metadata(
            item=item,
            detail_url=detail_url,
            final_seq=final_seq,
            source_file_label=source_file_label,
            invalid_reason=invalid_reason,
        )
        dataset_name = metadata["파일데이터명"]
        column_rows = [make_empty_column_placeholder(dataset_name)]
        metadata = {col: metadata.get(col, "") for col in TARGET_METADATA_COLUMNS}
        return metadata, column_rows

    metadata = parse_metadata_target(
        soup=soup,
        item=item,
        detail_url=detail_url,
        final_seq=final_seq,
        source_file_label=source_file_label,
    )

    dataset_name = metadata["파일데이터명"]
    column_rows = parse_columns_target(soup, dataset_name, detail_url)
    metadata["컬럼목록"] = make_column_list(column_rows)

    # 컬럼정보 탭/표가 없는 파일데이터도 내부 column_rows placeholder는 유지합니다.
    # 단, 최종 산출물에서는 컬럼정보.xlsx를 저장하지 않습니다.
    if not column_rows:
        column_rows = [make_empty_column_placeholder(dataset_name)]

    metadata["컬럼목록"] = make_column_list(column_rows)
    metadata = {col: metadata.get(col, "") for col in TARGET_METADATA_COLUMNS}

    return metadata, column_rows

# ==========================================================
# 5. 상세 수집: httpx 하이브리드 방식
# ==========================================================

def build_http_headers():
    """
    상세페이지는 JS 실행이 필요 없는 HTML 중심이므로 httpx로 가져옵니다.
    우회 목적이 아니라 브라우저 렌더링 비용을 줄이기 위한 방식입니다.
    """
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Referer": "https://www.data.go.kr/",
    }

async def fetch_detail_httpx_with_retry(client, item, final_seq, source_file_label, worker_id, block_state, block_lock, defer_block=True):
    url = item.get("detail_url", "")
    url_candidates = make_detail_url_candidates(url)
    total_attempts = MAX_DETAIL_RETRIES + 1
    last_err = ""

    for attempt in range(1, total_attempts + 1):
        try:
            await wait_global_cooldown(block_state)
            await asyncio.sleep(random.uniform(DETAIL_JITTER_MIN_SEC, DETAIL_JITTER_MAX_SEC))

            candidate_errors = []
            for candidate_url in url_candidates:
                try:
                    resp = await client.get(candidate_url)
                    status = resp.status_code

                    if status in [403, 429, 500, 502, 503, 504]:
                        raise RuntimeError(f"HTTP status={status}, url={candidate_url}")

                    html = resp.text
                    if not html or len(html) < SHORT_HTML_MIN_LEN:
                        raise RuntimeError(
                            f"EMPTY_OR_SHORT_HTML status={status}, len={len(html) if html else 0}, url={candidate_url}"
                        )

                    # 차단/오류 문구 확인
                    soup_for_check = BeautifulSoup(html, "lxml")
                    body_text = clean_text(soup_for_check.get_text(" "))
                    blocked, signal = looks_blocked_text(body_text)
                    if blocked:
                        raise RuntimeError(f"BLOCK_SIGNAL: {signal}, url={candidate_url}")

                    # 대체 URL로 성공해도 최종 저장 URL은 원 URL 유지
                    parse_item = dict(item)
                    parse_item["detail_url"] = url

                    metadata, cols = parse_detail_html(
                        html=html,
                        item=parse_item,
                        final_seq=final_seq,
                        source_file_label=source_file_label,
                    )

                    return {
                        "ok": True,
                        "metadata": metadata,
                        "columns": cols,
                        "fail": None,
                        "blocked": False,
                        "deferred": False,
                    }

                except Exception as candidate_e:
                    candidate_errors.append(repr(candidate_e))
                    continue

            raise RuntimeError(" | ".join(candidate_errors) if candidate_errors else "NO_URL_CANDIDATE")

        except Exception as e:
            err = repr(e)
            last_err = err

            is_block = (
                "BLOCK_SIGNAL" in err
                or "status=403" in err
                or "status=429" in err
                or "HTTP status=403" in err
                or "HTTP status=429" in err
            )
            is_short = is_short_html_error(err)

            if is_block:
                await register_block_signal(block_state, block_lock, err)

                # 메인 수집 중에는 같은 URL을 오래 붙잡지 않고 뒤로 넘김
                if defer_block and DEFER_BLOCKED_URLS:
                    return {
                        "ok": False,
                        "metadata": None,
                        "columns": [],
                        "blocked": True,
                        "deferred": True,
                        "deferred_reason": "block",
                        "item": item,
                        "seq": final_seq,
                        "first_error": err,
                        "fail": {
                            "수집시각": now_str(),
                            "단계": "deferred_block_httpx",
                            "파일데이터명": item.get("title", ""),
                            "URL": url,
                            "최종순번": final_seq,
                            "조회수": item.get("조회수", ""),
                            "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
                            "오류": err,
                            "Traceback": traceback.format_exc(),
                        },
                    }

            # status=200 + 짧은 HTML은 같은 httpx 반복보다 브라우저 회수 성공률이 높습니다.
            # 마지막 시도까지 실패한 뒤 Playwright 회수 대상으로 넘깁니다.
            if is_short and PLAYWRIGHT_FALLBACK_FOR_SHORT_HTML and attempt >= total_attempts:
                return {
                    "ok": False,
                    "metadata": None,
                    "columns": [],
                    "blocked": False,
                    "deferred": True,
                    "deferred_reason": "short_html",
                    "item": item,
                    "seq": final_seq,
                    "first_error": err,
                    "fail": {
                        "수집시각": now_str(),
                        "단계": "deferred_short_html_httpx",
                        "파일데이터명": item.get("title", ""),
                        "URL": url,
                        "최종순번": final_seq,
                        "조회수": item.get("조회수", ""),
                        "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
                        "오류": err,
                        "Traceback": traceback.format_exc(),
                    },
                }

            if attempt < total_attempts:
                if is_block:
                    wait_sec = RETRY_BASE_DELAY_SEC * (attempt + 1) + random.uniform(1.5, 3.5)
                elif is_short:
                    wait_sec = min(5.0, RETRY_BASE_DELAY_SEC * 0.5 * attempt) + random.uniform(0.3, 0.8)
                else:
                    wait_sec = RETRY_BASE_DELAY_SEC * attempt + random.uniform(0.3, 1.0)

                print(f"  ⚠ 재시도 {attempt}/{total_attempts} worker={worker_id}, seq={final_seq}, wait={wait_sec:.1f}s, err={err}")
                await asyncio.sleep(wait_sec)
            else:
                return {
                    "ok": False,
                    "metadata": None,
                    "columns": [],
                    "blocked": is_block,
                    "deferred": False,
                    "fail": {
                        "수집시각": now_str(),
                        "단계": "fetch_detail_httpx",
                        "파일데이터명": item.get("title", ""),
                        "URL": url,
                        "최종순번": final_seq,
                        "조회수": item.get("조회수", ""),
                        "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
                        "오류": f"최종오류={err} | 마지막오류={last_err}",
                        "Traceback": traceback.format_exc(),
                    },
                }

    return {
        "ok": False,
        "metadata": None,
        "columns": [],
        "blocked": ("429" in last_err or "403" in last_err or "BLOCK_SIGNAL" in last_err),
        "deferred": False,
        "fail": {
            "수집시각": now_str(),
            "단계": "fetch_detail_httpx_last_error",
            "파일데이터명": item.get("title", ""),
            "URL": url,
            "최종순번": final_seq,
            "조회수": item.get("조회수", ""),
            "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
            "오류": last_err or "unknown",
            "Traceback": "",
        },
    }

async def fetch_detail_playwright_fallback(browser, item, final_seq, source_file_label, worker_id):
    """
    httpx에서 403/429/응답 오류로 끝까지 실패한 URL만 최종적으로 Playwright로 회수합니다.
    대량 수집은 httpx로 유지하고, 실패 URL 몇 건만 브라우저로 처리하므로 전체 속도 영향은 작습니다.
    """
    url = item.get("detail_url", "")
    page = None

    try:
        context = await browser.new_context(
            locale="ko-KR",
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
            },
        )
        await setup_route(context)

        page = await context.new_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)

        status = response.status if response else None
        if status in [403, 429, 500, 502, 503, 504]:
            raise RuntimeError(f"PLAYWRIGHT_FALLBACK_HTTP_STATUS={status}")

        await wait_detail_ready(page)

        html = await page.content()
        body_text = clean_text(await page.locator("body").inner_text(timeout=3000))
        blocked, signal = looks_blocked_text(body_text)

        if blocked:
            raise RuntimeError(f"PLAYWRIGHT_FALLBACK_BLOCK_SIGNAL: {signal}")

        metadata, cols = parse_detail_html(
            html=html,
            item=item,
            final_seq=final_seq,
            source_file_label=source_file_label,
        )

        await safe_close_context(context)

        return {
            "ok": True,
            "metadata": metadata,
            "columns": cols,
            "fail": None,
        }

    except Exception as e:
        try:
            if page is not None:
                await page.close()
        except Exception:
            pass

        try:
            await safe_close_context(context)
        except Exception:
            pass

        return {
            "ok": False,
            "metadata": None,
            "columns": [],
                "fail": {
                "수집시각": now_str(),
                "단계": "playwright_fallback_failed",
                "파일데이터명": item.get("title", ""),
                "URL": url,
                "최종순번": final_seq,
                "조회수": item.get("조회수", ""),
                "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
                "오류": repr(e),
                "Traceback": traceback.format_exc(),
            },
        }

async def recover_failed_with_playwright(fallback_items, source_file_label, results, force=False):
    """
    httpx 최종 실패 URL을 Playwright로 한 번 더 회수합니다.
    force=True이면 전역 PLAYWRIGHT_FALLBACK_FOR_FAILED=False 상태에서도 회수합니다.
    """
    if not fallback_items:
        return
    if not force and not PLAYWRIGHT_FALLBACK_FOR_FAILED:
        return

    wait_sec = random.uniform(PLAYWRIGHT_FALLBACK_WAIT_MIN_SEC, PLAYWRIGHT_FALLBACK_WAIT_MAX_SEC)
    print(f"\n[Playwright 최종 회수] {len(fallback_items)}건, 시작 전 {wait_sec:.1f}초 대기")
    await asyncio.sleep(wait_sec)

    queue = asyncio.Queue()
    for d in fallback_items:
        queue.put_nowait(d)

    lock = asyncio.Lock()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)

        async def recovery_worker(worker_id):
            while not queue.empty():
                try:
                    d = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                seq = d["seq"]
                item = d["item"]
                first_error = d.get("first_error", "")
                httpx_final_error = d.get("httpx_final_error", "")

                print(f"[Playwright 회수] seq={seq}, worker={worker_id}")

                result = await fetch_detail_playwright_fallback(
                    browser=browser,
                    item=item,
                    final_seq=seq,
                    source_file_label=source_file_label,
                    worker_id=worker_id,
                )

                async with lock:
                    if result["ok"]:
                        results["metadata_rows"].append(result["metadata"])
                        results["column_rows"].extend(result["columns"])
                        print(f"  ✅ Playwright 회수 완료 seq={seq}, 컬럼={len(result['columns'])}")
                    else:
                        fail = result["fail"]
                        fail["오류"] = (
                            f"{fail.get('오류', '')} | "
                            f"최초보류오류={first_error} | "
                            f"httpx최종오류={httpx_final_error}"
                        )
                        results["fail_rows"].append(fail)
                        print(f"  ❌ Playwright 회수 실패 seq={seq}, err={fail.get('오류', '')}")

                queue.task_done()

        workers = [
            asyncio.create_task(recovery_worker(i + 1))
            for i in range(max(1, PLAYWRIGHT_FALLBACK_CONCURRENCY))
        ]
        await asyncio.gather(*workers)

        await safe_close_browser(browser)

async def collect_details_httpx_concurrent(items, source_file_label, concurrency, output_dir, defer_block=True):
    """
    상세 URL 수집은 httpx 비동기 요청으로 처리합니다.
    응답 제한/오류 URL은 후순위 재시도 후, 그래도 실패하면 Playwright로 최종 회수합니다.
    """
    if httpx is None:
        raise RuntimeError("httpx가 설치되어 있지 않습니다. pip install httpx 를 실행하세요.")

    queue = asyncio.Queue()
    for idx, item in enumerate(items, start=1):
        queue.put_nowait((idx, item))

    results = {
        "metadata_rows": [],
        "column_rows": [],
        "fail_rows": [],
    }

    deferred_items = []
    playwright_fallback_items = []

    lock = asyncio.Lock()
    block_lock = asyncio.Lock()
    block_state = {"count": 0, "cooldown_until": 0.0}
    start = time.perf_counter()

    timeout = httpx.Timeout(
        connect=8.0,
        read=max(8.0, DETAIL_TIMEOUT_MS / 1000),
        write=8.0,
        pool=8.0,
    )

    limits = httpx.Limits(
        max_connections=max(8, concurrency * 2),
        max_keepalive_connections=max(4, concurrency),
        keepalive_expiry=30.0,
    )

    async with httpx.AsyncClient(
        headers=build_http_headers(),
        timeout=timeout,
        limits=limits,
        follow_redirects=True,
        http2=False,
        verify=True,
    ) as client:

        async def worker(worker_id):
            while not queue.empty():
                try:
                    seq, item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                if VERBOSE_DETAIL_LOG:
                    print(f"[DETAIL] worker={worker_id}, seq={seq}, title={item.get('title','')}")

                result = await fetch_detail_httpx_with_retry(
                    client=client,
                    item=item,
                    final_seq=seq,
                    source_file_label=source_file_label,
                    worker_id=worker_id,
                    block_state=block_state,
                    block_lock=block_lock,
                    defer_block=defer_block,
                )

                async with lock:
                    if result["ok"]:
                        results["metadata_rows"].append(result["metadata"])
                        results["column_rows"].extend(result["columns"])
                        if SHOW_EACH_SUCCESS or VERBOSE_DETAIL_LOG:
                            print(f"  ✅ 완료 seq={seq}, 컬럼={len(result['columns'])}")
                    elif result.get("deferred"):
                        deferred_items.append({
                            "seq": result["seq"],
                            "item": result["item"],
                            "first_error": result.get("first_error", ""),
                            "reason": result.get("deferred_reason", "block"),
                        })
                        if SHOW_EACH_FAILURE:
                            print(f"  ⏭ 후순위 이동 seq={seq}, err={result.get('first_error', '')}")
                    else:
                        # 일반 오류는 바로 실패 처리. 403/429 등은 fetch 단계에서 deferred로 빠지는 것이 기본.
                        results["fail_rows"].append(result["fail"])
                        if SHOW_EACH_FAILURE:
                            print(f"  ❌ 실패 seq={seq}, blocked={result.get('blocked')}, err={result.get('fail', {}).get('오류', '')}")

                    done = len(results["metadata_rows"]) + len(results["fail_rows"]) + len(deferred_items)
                    if CHECKPOINT_EVERY > 0 and done % CHECKPOINT_EVERY == 0:
                        print(f"\n[중간 저장] {done}건 처리")
                        save_outputs(output_dir, **results, quiet=True)

                    if done % DETAIL_PROGRESS_EVERY == 0 or done == len(items):
                        print_progress(
                            prefix="⭐️DETAIL",
                            done=done,
                            total=len(items),
                            ok=len(results["metadata_rows"]),
                            fail=len(results["fail_rows"]) + len(deferred_items),
                            extra=f"보류 {len(deferred_items)}",
                            start_time=start,
                        )

                queue.task_done()

        workers = [asyncio.create_task(worker(i + 1)) for i in range(concurrency)]
        await asyncio.gather(*workers)

        # v6.2:
        # 403/429 URL은 같은 httpx 방식으로 재시도해도 실패할 가능성이 높으므로,
        # 기본값에서는 후순위 httpx 재시도를 생략하고 바로 Playwright 회수 대상으로 넘깁니다.
        if deferred_items:
            short_html_items = [d for d in deferred_items if d.get("reason") == "short_html"]
            block_items = [d for d in deferred_items if d.get("reason") != "short_html"]

            print(f"\n[후순위 처리] 보류 URL {len(deferred_items)}건")
            if short_html_items:
                print(f"- 짧은 HTML 응답: {len(short_html_items)}건 → Playwright 즉시 회수 대상")
            if block_items:
                print(f"- 403/429/차단 신호: {len(block_items)}건")

            # status=200 + EMPTY_OR_SHORT_HTML은 차단이라기보다 짧은 안내/전환 응답인 경우가 많아
            # 메인 흐름 안에서 Playwright로 즉시 회수합니다.
            for d in short_html_items:
                playwright_fallback_items.append({
                    "seq": d["seq"],
                    "item": d["item"],
                    "first_error": d.get("first_error", ""),
                    "httpx_final_error": "EMPTY_OR_SHORT_HTML → Playwright fallback",
                    "force": True,
                })

            if block_items:
                if SAVE_FAILED_URLS_ONLY:
                    # 403/429 차단류는 즉시 재접속보다 시간차 재수집이 안전합니다.
                    for d in block_items:
                        item = d.get("item", {})
                        results["fail_rows"].append({
                            "수집시각": now_str(),
                            "단계": "deferred_saved_only",
                            "파일데이터명": item.get("title", ""),
                            "URL": item.get("detail_url", ""),
                            "최종순번": d.get("seq", ""),
                            "조회수": item.get("조회수", ""),
                            "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
                            "오류": d.get("first_error", ""),
                            "Traceback": "",
                        })
                    print("[후순위 처리] 403/429 차단류는 실패로그에 기록 후 RETRY_FAILED/BOTH에서 시간차 재수집")
                elif SKIP_DEFERRED_HTTPX_RETRY:
                    for d in block_items:
                        playwright_fallback_items.append({
                            "seq": d["seq"],
                            "item": d["item"],
                            "first_error": d.get("first_error", ""),
                            "httpx_final_error": "SKIP_DEFERRED_HTTPX_RETRY=True",
                        })
                    print(f"[후순위 처리] httpx 재시도 생략 → Playwright 회수 대상 {len(block_items)}건")
                elif RETRY_DEFERRED_AFTER_MAIN:
                    wait_sec = random.uniform(DEFERRED_RETRY_WAIT_MIN_SEC, DEFERRED_RETRY_WAIT_MAX_SEC)
                    print(f"\n[후순위 httpx 재시도] {len(block_items)}건, 시작 전 {wait_sec:.1f}초 대기")
                    await asyncio.sleep(wait_sec)

                    retry_queue = asyncio.Queue()
                    for d in block_items:
                        retry_queue.put_nowait(d)

                    async def retry_worker(worker_id):
                        while not retry_queue.empty():
                            try:
                                d = retry_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                return

                            seq = d["seq"]
                            item = d["item"]
                            print(f"[후순위 httpx 재시도] seq={seq}, worker={worker_id}")

                            result = await fetch_detail_httpx_with_retry(
                                client=client,
                                item=item,
                                final_seq=seq,
                                source_file_label=source_file_label,
                                worker_id=f"R{worker_id}",
                                block_state=block_state,
                                block_lock=block_lock,
                                defer_block=False,
                            )

                            async with lock:
                                if result["ok"]:
                                    results["metadata_rows"].append(result["metadata"])
                                    results["column_rows"].extend(result["columns"])
                                    print(f"  ✅ 후순위 httpx 완료 seq={seq}, 컬럼={len(result['columns'])}")
                                else:
                                    fail = result["fail"]
                                    first_error = d.get("first_error", "")
                                    httpx_final_error = fail.get("오류", "")
                                    if PLAYWRIGHT_FALLBACK_FOR_FAILED:
                                        playwright_fallback_items.append({
                                            "seq": seq,
                                            "item": item,
                                            "first_error": first_error,
                                            "httpx_final_error": httpx_final_error,
                                        })
                                        print(f"  🔁 Playwright 최종 회수 대기 seq={seq}, err={httpx_final_error}")
                                    else:
                                        fail["오류"] = f"{httpx_final_error} | 최초보류오류={first_error}"
                                        results["fail_rows"].append(fail)
                                        print(f"  ❌ 후순위 httpx 실패 seq={seq}, err={fail.get('오류', '')}")

                            retry_queue.task_done()

                    retry_workers = [
                        asyncio.create_task(retry_worker(i + 1))
                        for i in range(max(1, DEFERRED_RETRY_CONCURRENCY))
                    ]
                    await asyncio.gather(*retry_workers)

    # httpx로 끝까지 실패한 URL만 Playwright로 최종 회수
    force_fallback_items = [d for d in playwright_fallback_items if d.get("force")]
    normal_fallback_items = [d for d in playwright_fallback_items if not d.get("force")]

    # EMPTY_OR_SHORT_HTML은 즉시 회수(force=True), 403/429류는 기존 설정을 따릅니다.
    await recover_failed_with_playwright(
        fallback_items=force_fallback_items,
        source_file_label=source_file_label,
        results=results,
        force=True,
    )
    await recover_failed_with_playwright(
        fallback_items=normal_fallback_items,
        source_file_label=source_file_label,
        results=results,
        force=False,
    )

    return results

# ==========================================================
# 6. 저장
# ==========================================================

EXCEL_MAX_DATA_ROWS_PER_SHEET = 1_000_000


def make_safe_sheet_name(base_name, idx=None):
    name = clean_text(base_name) or "Sheet"
    name = re.sub(r"[\/\?\*\[\]:]", "_", name)
    if idx is not None:
        suffix = f"_{idx:03d}"
        name = name[:31 - len(suffix)] + suffix
    else:
        name = name[:31]
    return name or "Sheet"


def write_excel_no_url_warning(df, path, sheet_name="Sheet1", split_sheets=False, max_rows_per_sheet=EXCEL_MAX_DATA_ROWS_PER_SHEET):
    """
    - strings_to_urls=False: URL 자동 하이퍼링크 생성 방지
    - split_sheets=True: Excel 한 시트 최대 행 수 초과 방지를 위해 여러 시트로 분할 저장
    """
    path = Path(path)
    max_rows_per_sheet = int(max_rows_per_sheet or EXCEL_MAX_DATA_ROWS_PER_SHEET)
    max_rows_per_sheet = max(1, min(max_rows_per_sheet, 1_048_575))  # 헤더 1행 제외 안전 한도

    def write_to_writer(writer):
        if split_sheets and len(df) > max_rows_per_sheet:
            total = len(df)
            part_no = 1
            for start in range(0, total, max_rows_per_sheet):
                end = min(start + max_rows_per_sheet, total)
                part_df = df.iloc[start:end]
                part_sheet = make_safe_sheet_name(sheet_name, part_no)
                part_df.to_excel(writer, index=False, sheet_name=part_sheet)
                part_no += 1
        else:
            df.to_excel(writer, index=False, sheet_name=make_safe_sheet_name(sheet_name))

    try:
        with pd.ExcelWriter(
            path,
            engine="xlsxwriter",
            engine_kwargs={"options": {"strings_to_urls": False}},
        ) as writer:
            write_to_writer(writer)
    except ModuleNotFoundError:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            write_to_writer(writer)


def normalize_column_output_df(df):
    """
    컬럼정보 결과를 최종 출력 스키마로 정리합니다.
    - 구버전 컬럼명(항목 설명, 최대길이)을 신버전 컬럼명으로 이전
    - 메타데이터 표가 컬럼정보로 섞인 행 제거
    - 데이터항목(컬럼) 정보가 없는 페이지는 파일데이터명만 채운 빈 행을 유지
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=COLUMN_OUTPUT_COLUMNS)

    df = df.copy()

    if "항목설명" not in df.columns and "항목 설명" in df.columns:
        df["항목설명"] = df["항목 설명"]
    elif "항목설명" in df.columns and "항목 설명" in df.columns:
        df["항목설명"] = df["항목설명"].where(df["항목설명"].astype(str).str.strip() != "", df["항목 설명"])

    if "데이터 길이" not in df.columns and "최대길이" in df.columns:
        df["데이터 길이"] = df["최대길이"]
    elif "데이터 길이" in df.columns and "최대길이" in df.columns:
        df["데이터 길이"] = df["데이터 길이"].where(df["데이터 길이"].astype(str).str.strip() != "", df["최대길이"])

    for col in COLUMN_OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df.reindex(columns=COLUMN_OUTPUT_COLUMNS)

    if "항목명" in df.columns:
        file_names = df["파일데이터명"].fillna("").astype(str).map(clean_text)
        item_names = df["항목명"].fillna("").astype(str).map(clean_text)

        value_cols = ["항목설명", "데이터타입", "데이터 길이"]
        has_value = pd.Series(False, index=df.index)
        for col in value_cols:
            has_value |= df[col].fillna("").astype(str).map(clean_text) != ""

        # 실제 컬럼정보 행: 항목명이 있고, 메타데이터 라벨이 아니며, 상세값이 하나 이상 있는 행
        real_column_row = item_names != ""
        real_column_row &= ~item_names.map(lambda x: norm_key(x) in COLUMN_SKIP_LABELS)
        real_column_row &= has_value

        # 컬럼정보가 없는 파일데이터용 placeholder 행: 파일데이터명만 있고 나머지 정보가 비어 있는 행
        placeholder_row = file_names != ""
        placeholder_row &= item_names == ""
        placeholder_row &= ~has_value

        df = df[real_column_row | placeholder_row].copy()

    if not df.empty:
        file_names = df["파일데이터명"].fillna("").astype(str).map(clean_text)
        item_names = df["항목명"].fillna("").astype(str).map(clean_text)
        value_cols = ["항목설명", "데이터타입", "데이터 길이"]
        has_value = pd.Series(False, index=df.index)
        for col in value_cols:
            has_value |= df[col].fillna("").astype(str).map(clean_text) != ""

        placeholder_row = (file_names != "") & (item_names == "") & (~has_value)
        real_idx = df.index[~placeholder_row]

        if len(real_idx) > 0:
            seq = pd.to_numeric(df.loc[real_idx, "순번"], errors="coerce")
            if seq.isna().any():
                df.loc[real_idx, "순번"] = df.loc[real_idx].groupby("상세페이지 URL", dropna=False).cumcount() + 1
            else:
                df.loc[real_idx, "순번"] = seq.astype(int)

        # placeholder 행은 파일데이터명 외 모든 필드를 빈칸으로 유지합니다.
        df.loc[placeholder_row, ["상세페이지 URL", "순번", "항목명", "항목설명", "데이터타입", "데이터 길이"]] = ""

    return df.reindex(columns=COLUMN_OUTPUT_COLUMNS)


def save_outputs(output_dir, metadata_rows, column_rows, fail_rows, quiet=False):
    """
    최종 결과 파일은 메타데이터.xlsx와 실패로그.xlsx만 저장합니다.

    주의:
    - column_rows는 메타데이터의 "컬럼목록" 생성을 위해 내부적으로는 계속 수집됩니다.
    - 다만 사용자가 최종 산출물에서 컬럼정보.xlsx 파일은 제외하기로 했으므로,
      별도 컬럼정보.xlsx는 저장하지 않습니다.
    - 이전 실행에서 남아 있는 컬럼정보.xlsx가 있으면 혼동 방지를 위해 삭제합니다.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata_df = pd.DataFrame(metadata_rows)
    fail_df = pd.DataFrame(fail_rows)

    if not metadata_df.empty and "최종순번" in metadata_df.columns:
        metadata_df = metadata_df.sort_values("최종순번", kind="stable")

    metadata_df = metadata_df.reindex(columns=TARGET_METADATA_COLUMNS)
    fail_df = fail_df.reindex(columns=FAIL_COLUMNS)

    paths = {
        "metadata": output_dir / "메타데이터.xlsx",
        "fail": output_dir / "실패로그.xlsx",
    }

    # 이전 버전에서 생성된 컬럼정보.xlsx가 같은 폴더에 남아 있으면 삭제합니다.
    old_columns_path = output_dir / "컬럼정보.xlsx"
    if old_columns_path.exists():
        try:
            old_columns_path.unlink()
            if not quiet:
                print(f"- 기존 컬럼정보.xlsx 삭제: {old_columns_path}")
        except Exception as e:
            print(f"[경고] 기존 컬럼정보.xlsx 삭제 실패: {repr(e)}")

    write_excel_no_url_warning(metadata_df, paths["metadata"], sheet_name="메타데이터")
    write_excel_no_url_warning(fail_df, paths["fail"], sheet_name="실패로그")

    if not quiet:
        print("\n[저장 완료]")
        for k, path in paths.items():
            print(f"- {k}: {path}")

    return {
        "metadata_df": metadata_df,
        "fail_df": fail_df,
        "paths": {k: str(v) for k, v in paths.items()},
    }

def zip_output_folder(output_dir):
    zip_path = shutil.make_archive(str(output_dir), "zip", str(output_dir))
    print(f"[ZIP 생성] {zip_path}")
    return zip_path

# ==========================================================
# 7. 실행 본체
# ==========================================================

async def run_crawler_async():
    settings = load_settings()

    job_name = settings["job_name"]
    target_url = settings["target_url"]
    max_pages = int(settings["max_pages"])
    max_detail_items = int(settings["max_detail_items"])
    headless = bool(settings["headless"])
    source_file_label = settings.get("source_file_label") or "실시간수집"
    list_per_page = int(settings.get("list_per_page", LIST_PER_PAGE))
    concurrency = int(settings.get("detail_concurrency", DETAIL_CONCURRENCY))
    make_zip = bool(settings.get("make_zip", MAKE_ZIP))

    output_dir = make_output_dir(job_name)
    start = time.perf_counter()

    print("=" * 80)
    print("[공공데이터포털 메타데이터 수집]")
    print(f"- 작업명: {job_name}")
    print(f"- URL: {target_url}")
    print(f"- MAX_PAGES: {max_pages}")
    print(f"- MAX_DETAIL_ITEMS: {max_detail_items}")
    print(f"- HEADLESS: {headless}")
    print(f"- LIST_PER_PAGE: {list_per_page}")
    print(f"- DETAIL_CONCURRENCY: {concurrency}")
    print(f"- DETAIL_FETCH: httpx hybrid")
    print(f"- DETAIL_JITTER: {DETAIL_JITTER_MIN_SEC}~{DETAIL_JITTER_MAX_SEC}s")
    print(f"- SHORT_HTML_MIN_LEN: {SHORT_HTML_MIN_LEN}")
    print(f"- PLAYWRIGHT_FALLBACK_FOR_SHORT_HTML: {PLAYWRIGHT_FALLBACK_FOR_SHORT_HTML}")
    print(f"- EXCEL_STRINGS_TO_URLS: False")
    print(f"- ENABLE_MAIN_COOLDOWN: {ENABLE_MAIN_COOLDOWN}")
    print(f"- BLOCK_COOLDOWN: {BLOCK_COOLDOWN_BASE_SEC}~{BLOCK_COOLDOWN_MAX_SEC}s")
    print(f"- DEFERRED_RETRY_CONCURRENCY: {DEFERRED_RETRY_CONCURRENCY}")
    print(f"- SKIP_DEFERRED_HTTPX_RETRY: {SKIP_DEFERRED_HTTPX_RETRY}")
    print(f"- SAVE_FAILED_URLS_ONLY: {SAVE_FAILED_URLS_ONLY}")
    print(f"- PLAYWRIGHT_FALLBACK_FOR_FAILED: {PLAYWRIGHT_FALLBACK_FOR_FAILED}")
    print(f"- PLAYWRIGHT_FALLBACK_CONCURRENCY: {PLAYWRIGHT_FALLBACK_CONCURRENCY}")
    print(f"- MAKE_ZIP: {make_zip}")
    print(f"- OUTPUT_DIR: {output_dir}")
    print("=" * 80)

    if httpx is None:
        raise RuntimeError("httpx가 설치되어 있지 않습니다. pip install httpx 를 실행하세요.")

    results = {
        "metadata_rows": [],
        "column_rows": [],
        "fail_rows": [],
    }

    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)

            try:
                items = await collect_list_items(
                    browser=browser,
                    target_url=target_url,
                    max_pages=max_pages,
                    max_detail_items=max_detail_items,
                    list_per_page=list_per_page,
                )
            finally:
                await safe_close_browser(browser)

        print(f"\n[⭐️상세 URL 수집 완료⭐️] {len(items)}건")

        results = await collect_details_httpx_concurrent(
            items=items,
            source_file_label=source_file_label,
            concurrency=concurrency,
            output_dir=output_dir,
            defer_block=True,
        )

    except Exception as e:
        err = repr(e)
        print(f"[✴️경고✴️] 실행 중 오류 발생. 가능한 부분 결과를 저장합니다: {err}")
        results["fail_rows"].append({
            "수집시각": now_str(),
            "단계": "run_crawler_async_error",
            "파일데이터명": "",
            "URL": target_url,
            "최종순번": "",
            "오류": err,
            "Traceback": traceback.format_exc(),
        })

    result = save_outputs(
        output_dir=output_dir,
        metadata_rows=results["metadata_rows"],
        column_rows=results["column_rows"],
        fail_rows=results["fail_rows"],
    )

    if make_zip:
        result["zip_path"] = zip_output_folder(output_dir)
    else:
        result["zip_path"] = ""
        print("[ZIP 생략] MAKE_ZIP=False")

    elapsed = time.perf_counter() - start
    processed = len(results["metadata_rows"]) + len(results["fail_rows"])
    speed = processed / elapsed if elapsed > 0 else 0

    print("\n" + "=" * 80)
    print("[⭐️전체 완료⭐️]")
    print(f"- 처리 상세 건수: {processed}")
    print(f"- 성공 rows: {len(results['metadata_rows'])}")
    print(f"- 컬럼목록 내부 rows: {len(results['column_rows'])}")
    print(f"- 실패 rows: {len(results['fail_rows'])}")
    print(f"- 소요 시간: {elapsed:.2f}초")
    print(f"- 평균 속도: {speed:.2f}건/초")
    print("=" * 80)

    return result

# ==========================================================
# 8. 실패로그 기준 /Playwright 재수집
# ==========================================================

def resolve_main_output_dir():
    settings = load_settings()
    return Path(make_output_dir(settings["job_name"]))

def resolve_retry_paths():
    output_dir = resolve_main_output_dir()

    fail_log_path = Path(RETRY_FAIL_LOG_PATH) if RETRY_FAIL_LOG_PATH else output_dir / "실패로그.xlsx"
    metadata_path = Path(RETRY_EXISTING_METADATA_PATH) if RETRY_EXISTING_METADATA_PATH else output_dir / "메타데이터.xlsx"
    columns_path = Path(RETRY_EXISTING_COLUMNS_PATH) if RETRY_EXISTING_COLUMNS_PATH else output_dir / "컬럼정보.xlsx"

    return output_dir, fail_log_path, metadata_path, columns_path

def sync_setup_route(context):
    if not BLOCK_RESOURCE_TYPES:
        return

    blocked_types = {"image", "media", "font", "stylesheet"}

    def route_handler(route):
        try:
            if route.request.resource_type in blocked_types:
                route.abort()
            else:
                route.continue_()
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    context.route("**/*", route_handler)

def sync_wait_detail_ready(page):
    try:
        page.wait_for_function(
            """() => {
                const t = document.body ? document.body.innerText : '';
                return t.includes('파일데이터명') || t.includes('분류체계') || t.includes('제공기관');
            }""",
            timeout=12000,
        )
    except Exception:
        try:
            page.wait_for_selector("body", timeout=5000)
        except Exception:
            pass

def has_retry_target_failures(fail_log_path):
    """
    실패로그.xlsx에 실제 재수집 대상이 있는지 확인합니다.
    실패로그가 없거나 비어 있으면 BOTH 모드에서 재수집을 생략합니다.
    """
    path = Path(fail_log_path)
    if not path.exists():
        print(f"[재수집 생략] 실패로그 파일이 없습니다: {path}")
        return False

    try:
        fail_df = pd.read_excel(path)
    except Exception as e:
        print(f"[재수집 생략] 실패로그 읽기 실패: {repr(e)}")
        return False

    if fail_df.empty:
        print("[재수집 생략] 실패로그가 비어 있습니다.")
        return False

    if "URL" not in fail_df.columns:
        print("[재수집 생략] 실패로그에 URL 컬럼이 없습니다.")
        return False

    urls = fail_df["URL"].dropna().astype(str).str.strip()
    urls = urls[urls.str.startswith("http")]

    if urls.empty:
        print("[재수집 생략] 실패로그에 재수집 가능한 URL이 없습니다.")
        return False

    print(f"[재수집 대상 확인] 실패 URL {len(urls.drop_duplicates())}건")
    return True

def read_retry_failed_items(fail_log_path):
    if not fail_log_path.exists():
        raise FileNotFoundError(f"실패로그 파일을 찾을 수 없습니다: {fail_log_path}")

    fail_df = pd.read_excel(fail_log_path)

    if fail_df.empty:
        print("[재수집] 실패로그가 비어 있습니다. 재수집 대상 없음.")
        return fail_df, []

    if "URL" not in fail_df.columns:
        raise RuntimeError("실패로그.xlsx에 URL 컬럼이 없습니다.")

    # v7.2:
    # 실패로그에 남은 상세 URL은 오류 유형과 관계없이 Playwright 재수집 대상으로 봅니다.
    # EMPTY_OR_SHORT_HTML, 파싱 오류, 본문 오탐 등도 회수 가능성이 있기 때문입니다.
    target_df = fail_df.copy()
    target_df = target_df.dropna(subset=["URL"])
    target_df["URL"] = target_df["URL"].astype(str).str.strip()
    target_df = target_df[target_df["URL"].str.startswith("http")]
    target_df = target_df[target_df["URL"].apply(is_detail_url)]

    # 같은 URL이 original fail + retry fail로 중복 남은 경우 마지막 행만 재시도합니다.
    target_df = target_df.drop_duplicates(subset=["URL"], keep="last")

    items = []
    for idx, row in target_df.iterrows():
        title = clean_text(row.get("파일데이터명", ""))
        url = clean_text(row.get("URL", ""))
        first_error = clean_text(row.get("오류", ""))

        seq_raw = row.get("최종순번", "")
        try:
            seq = int(seq_raw)
        except Exception:
            seq = len(items) + 1

        items.append({
            "seq": seq,
            "item": {
                "raw_title": title,
                "title": title,
                "확장자": "",
                "조회수": clean_text(row.get("조회수", "")),
                "다운로드(바로가기)": clean_text(row.get("다운로드(바로가기)", "")),
                "다운로드수": clean_text(row.get("다운로드(바로가기)", "")),
                "detail_url": url,
                "source_list_url": "",
                "first_error": first_error,
            },
            "first_error": first_error,
        })

    return fail_df, items

def auto_wait_before_retry(fail_df):
    if not RETRY_AUTO_WAIT:
        return

    if "수집시각" not in fail_df.columns:
        return

    dts = pd.to_datetime(fail_df["수집시각"], errors="coerce").dropna()
    if dts.empty:
        return

    last_dt = dts.max().to_pydatetime()
    elapsed = (datetime.now() - last_dt).total_seconds()
    remain = max(0, RETRY_WAIT_AFTER_FAIL_SEC - elapsed)

    if remain > 0:
        print(f"[재수집 자동대기] 마지막 실패 후 {elapsed:.1f}초 경과 → {remain:.1f}초 대기")
        time.sleep(remain)


def make_retry_url_candidates(url):
    """
    실패 URL 재수집에서도 메인 httpx와 동일한 상세 URL 후보를 사용합니다.
    """
    return make_detail_url_candidates(url)


def retry_one_failed_url(page, retry_item):
    seq = retry_item["seq"]
    item = retry_item["item"]
    original_url = item["detail_url"]
    first_error = retry_item.get("first_error", "")
    last_err = ""

    url_candidates = make_retry_url_candidates(original_url)

    for attempt in range(1, RETRY_MAX_RETRIES_PER_URL + 1):
        for candidate_url in url_candidates:
            try:
                print(f"\n[실패URL 재수집] seq={seq}, attempt={attempt}/{RETRY_MAX_RETRIES_PER_URL}")
                print(f"- {item.get('title', '')}")
                print(f"- {candidate_url}")

                response = page.goto(candidate_url, wait_until="domcontentloaded", timeout=DETAIL_TIMEOUT_MS)
                status = response.status if response else None

                if status in [403, 429, 500, 502, 503, 504]:
                    raise RuntimeError(f"RETRY_FAILED_HTTP_STATUS={status}")

                sync_wait_detail_ready(page)

                html = page.content()
                if not html or len(html) < 500:
                    raise RuntimeError(f"RETRY_FAILED_EMPTY_OR_SHORT_HTML status={status}, len={len(html) if html else 0}")

                body_text = clean_text(page.locator("body").inner_text(timeout=5000))
                blocked, signal = looks_blocked_text(body_text)

                if blocked:
                    raise RuntimeError(f"RETRY_FAILED_BLOCK_SIGNAL: {signal}")

                # 대체 URL로 성공하더라도 최종 결과 URL은 원래 상세 URL을 유지합니다.
                parse_item = dict(item)
                parse_item["detail_url"] = original_url

                metadata, cols = parse_detail_html(
                    html=html,
                    item=parse_item,
                    final_seq=seq,
                    source_file_label=RETRY_SOURCE_FILE_LABEL,
                )

                print(f"  ✅ 실패URL 재수집 성공 seq={seq}, 컬럼={len(cols)}")
                return {
                    "ok": True,
                    "metadata": metadata,
                    "columns": cols,
                    "fail": None,
                }

            except Exception as e:
                last_err = repr(e)
                print(f"✴️실패URL 재수집 실패 seq={seq}, url={candidate_url}, err={last_err}")

        if attempt < RETRY_MAX_RETRIES_PER_URL:
            if "429" in last_err or "403" in last_err or "BLOCK_SIGNAL" in last_err:
                wait_sec = random.uniform(RETRY_BLOCK_DELAY_MIN_SEC, RETRY_BLOCK_DELAY_MAX_SEC)
            else:
                wait_sec = random.uniform(5.0, 10.0)

            print(f"  - 다음 attempt 전 {wait_sec:.1f}초 대기")
            time.sleep(wait_sec)

    return {
        "ok": False,
        "metadata": None,
        "columns": [],
        "fail": {
            "수집시각": now_str(),
            "단계": "retry_failed_playwright",
            "파일데이터명": item.get("title", ""),
            "URL": original_url,
            "최종순번": seq,
            "조회수": item.get("조회수", ""),
            "다운로드(바로가기)": item.get("다운로드(바로가기)", "") or item.get("다운로드수", ""),
            "오류": f"최종오류={last_err} | 최초오류={first_error}",
            "Traceback": traceback.format_exc(),
        },
    }

def save_retry_failed_outputs(output_dir, metadata_rows, column_rows, retry_fail_rows, original_fail_df, metadata_path, columns_path):
    """
    실패 URL 재수집 결과를 최종 파일에 반영합니다.

    최종 출력:
    - 메타데이터.xlsx
    - 실패로그.xlsx

    column_rows와 columns_path는 기존 함수 호출 구조와 내부 컬럼목록 수집 흐름을 유지하기 위해
    인자로만 받으며, 별도 컬럼정보.xlsx 파일은 저장하지 않습니다.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    retry_meta_df = pd.DataFrame(metadata_rows).reindex(columns=TARGET_METADATA_COLUMNS)
    retry_fail_df = pd.DataFrame(retry_fail_rows).reindex(columns=FAIL_COLUMNS)

    # 1) 메타데이터 최종 병합
    if metadata_path.exists():
        base_meta = pd.read_excel(metadata_path)
    else:
        base_meta = pd.DataFrame(columns=TARGET_METADATA_COLUMNS)

    final_meta = pd.concat([base_meta, retry_meta_df], ignore_index=True)

    if "상세페이지 URL" in final_meta.columns:
        final_meta = final_meta.drop_duplicates(subset=["상세페이지 URL"], keep="last")

    if "최종순번" in final_meta.columns:
        final_meta = final_meta.sort_values("최종순번", kind="stable")

    final_meta = final_meta.reindex(columns=TARGET_METADATA_COLUMNS)

    # 2) 실패로그 최종 병합
    if not original_fail_df.empty and "URL" in original_fail_df.columns:
        success_urls = set(retry_meta_df.get("상세페이지 URL", pd.Series(dtype=str)).dropna().astype(str))
        retry_fail_urls = set(retry_fail_df.get("URL", pd.Series(dtype=str)).dropna().astype(str))

        # 성공한 URL은 실패로그에서 제거.
        # 재수집까지 실패한 URL은 original fail 행을 제거하고 retry fail 최종 행만 남깁니다.
        final_fail = original_fail_df[
            ~original_fail_df["URL"].astype(str).isin(success_urls | retry_fail_urls)
        ].copy()
    else:
        final_fail = pd.DataFrame(columns=FAIL_COLUMNS)

    if not retry_fail_df.empty:
        final_fail = pd.concat([final_fail, retry_fail_df], ignore_index=True)

    if not final_fail.empty and "URL" in final_fail.columns:
        final_fail = final_fail.drop_duplicates(subset=["URL"], keep="last")

    final_fail = final_fail.reindex(columns=FAIL_COLUMNS)

    # 최종 파일 2개에 덮어쓰기
    final_metadata_path = output_dir / "메타데이터.xlsx"
    final_fail_path = output_dir / "실패로그.xlsx"

    # 이전 버전에서 생성된 컬럼정보.xlsx가 같은 폴더에 남아 있으면 삭제합니다.
    old_columns_path = output_dir / "컬럼정보.xlsx"
    if old_columns_path.exists():
        try:
            old_columns_path.unlink()
            print(f"- 기존 컬럼정보.xlsx 삭제: {old_columns_path}")
        except Exception as e:
            print(f"[경고] 기존 컬럼정보.xlsx 삭제 실패: {repr(e)}")

    write_excel_no_url_warning(final_meta, final_metadata_path, sheet_name="메타데이터")
    write_excel_no_url_warning(final_fail, final_fail_path, sheet_name="실패로그")

    print("\n[최종 결과 저장 완료 - 파일 2개]")
    print(f"- {final_metadata_path}")
    print(f"- {final_fail_path}")

def run_retry_failed_with_playwright():
    output_dir, fail_log_path, metadata_path, columns_path = resolve_retry_paths()
    original_fail_df, retry_items = read_retry_failed_items(fail_log_path)

    print("=" * 80)
    print("[실패로그 기준 Playwright 재수집]")
    print(f"- 실패로그: {fail_log_path}")
    print(f"- 재수집 대상: {len(retry_items)}건")
    print(f"- RETRY_AUTO_WAIT: {RETRY_AUTO_WAIT}")
    print(f"- RETRY_WAIT_AFTER_FAIL_SEC: {RETRY_WAIT_AFTER_FAIL_SEC}")
    print(f"- URL_DELAY: {RETRY_URL_DELAY_MIN_SEC}~{RETRY_URL_DELAY_MAX_SEC}s")
    print("=" * 80)

    if not retry_items:
        return {
            "metadata_rows": [],
            "column_rows": [],
            "fail_rows": [],
        }

    auto_wait_before_retry(original_fail_df)

    metadata_rows = []
    column_rows = []
    retry_fail_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=RETRY_HEADLESS)
        context = browser.new_context(
            locale="ko-KR",
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Referer": "https://www.data.go.kr/",
            },
        )
        sync_setup_route(context)
        page = context.new_page()

        try:
            try:
                page.goto("https://www.data.go.kr/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                print(f"[재수집] 포털 메인 사전 접속 실패 무시: {repr(e)}")

            for retry_item in retry_items:
                result = retry_one_failed_url(page, retry_item)

                if result["ok"]:
                    metadata_rows.append(result["metadata"])
                    column_rows.extend(result["columns"])
                else:
                    retry_fail_rows.append(result["fail"])

                delay = random.uniform(RETRY_URL_DELAY_MIN_SEC, RETRY_URL_DELAY_MAX_SEC)
                print(f"  - 다음 실패 URL 전 {delay:.1f}초 대기")
                time.sleep(delay)

        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    save_retry_failed_outputs(
        output_dir=output_dir,
        metadata_rows=metadata_rows,
        column_rows=column_rows,
        retry_fail_rows=retry_fail_rows,
        original_fail_df=original_fail_df,
        metadata_path=metadata_path,
        columns_path=columns_path,
    )

    print("\n" + "=" * 80)
    print("[실패URL 재수집 완료]")
    print(f"- 성공: {len(metadata_rows)}건")
    print(f"- 실패: {len(retry_fail_rows)}건")
    print("=" * 80)

    return {
        "metadata_rows": metadata_rows,
        "column_rows": column_rows,
        "fail_rows": retry_fail_rows,
    }

def run_crawler():
    return asyncio.run(run_crawler_async())

def main():
    mode = str(RUN_MODE).upper().strip()

    if mode == "MAIN":
        run_crawler()

    elif mode == "RETRY_FAILED":
        run_retry_failed_with_playwright()

    elif mode == "BOTH":
        run_crawler()

        output_dir, fail_log_path, metadata_path, columns_path = resolve_retry_paths()
        if not has_retry_target_failures(fail_log_path):
            print("\n[BOTH 모드 종료] 실패로그 재수집 대상이 없어 최종 파일 2개로 종료합니다.")
            return

        if BOTH_MODE_WAIT_SEC > 0:
            print(f"\n[BOTH 모드 대기] 실패로그 재수집 전 {BOTH_MODE_WAIT_SEC}초 대기")
            time.sleep(BOTH_MODE_WAIT_SEC)

        # BOTH 모드에서는 위에서 이미 3분 대기했으므로 실패로그 기준 자동대기는 잠시 끕니다.
        global RETRY_AUTO_WAIT
        _old_retry_auto_wait = RETRY_AUTO_WAIT
        RETRY_AUTO_WAIT = False
        try:
            run_retry_failed_with_playwright()
        finally:
            RETRY_AUTO_WAIT = _old_retry_auto_wait

    else:
        raise ValueError(f"지원하지 않는 RUN_MODE입니다: {RUN_MODE}")

if __name__ == "__main__":
    main()
