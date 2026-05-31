# -*- coding: utf-8 -*-
"""
page1_org_metadata.py

메타데이터 크롤링 > 기관별 수집 탭.
- 기관별 수집도 crawler_metadata.py의 전체 수집 엔진과 동일한 collect_list_items + collect_details_httpx_concurrent 사용
- 전체 메타데이터 수집과 기관별 메타데이터 수집의 상세 URL 수집/파싱/실패로그 방식을 통일
- 실패 URL은 조용히 누락하지 않고 실패로그 시트에 기록
"""

import asyncio
import contextlib
import io
import math
import os
import random
import re
import subprocess
import sys
import time
import traceback
from io import BytesIO

import httpx
import pandas as pd
import streamlit as st

import crawler_metadata


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def _progress_int(value, default=0):
    try:
        if value is None:
            return default
        return int(str(value).replace(",", "").strip())
    except Exception:
        return default


def _is_playwright_browser_missing_error(exc) -> bool:
    """
    Playwright 패키지는 설치됐지만 Chromium 실행 파일이 없는 경우만 감지합니다.
    기존 크롤링/URL 수집 로직과는 분리된 사전 점검용 함수입니다.
    """
    msg = str(exc)
    return (
        "Executable doesn't exist" in msg
        or "playwright install" in msg
        or "chromium_headless_shell" in msg
        or "browserType.launch" in msg
    )


def _install_playwright_chromium_for_precheck():
    """
    기관검색 전에 Chromium 브라우저 파일만 사전 설치합니다.
    crawler_metadata.py 내부의 수집 시작 시 점검/설치 로직은 그대로 유지됩니다.
    """
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=600,
    )
    output = proc.stdout or ""
    if proc.returncode != 0:
        raise RuntimeError(output[-4000:] or "playwright install chromium 실패")
    return output


def _prepare_required_runtime_before_org_search():
    """
    기관검색 전에 필수 실행 환경을 사전 점검합니다.
    - Chromium 실행 가능 여부 확인
    - 미설치 오류일 때만 playwright install chromium 실행
    - 설치 후 실제 launch까지 재확인

    주의: 기관검색, URL 생성, 상세 URL 수집, 상세 HTML 수집 로직은 수정하지 않습니다.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
                return True, "Playwright Chromium 실행 환경이 이미 준비되어 있습니다."
            except Exception as first_error:
                if not _is_playwright_browser_missing_error(first_error):
                    raise

                install_output = _install_playwright_chromium_for_precheck()

                browser = p.chromium.launch(headless=True)
                browser.close()

                brief = install_output[-1200:].strip()
                if brief:
                    return True, "Playwright Chromium 설치 및 실행 점검이 완료되었습니다."
                return True, "Playwright Chromium 설치 및 실행 점검이 완료되었습니다."

    except Exception as e:
        return False, f"필수 실행 환경 준비 중 오류가 발생했습니다: {repr(e)}"


class _StreamlitProgressStdout:
    """
    crawler_metadata.py의 기존 print 로그를 그대로 받으면서
    Streamlit 진행바만 실시간으로 갱신합니다.
    """
    def __init__(self, buffer, progress_bar, status_box=None, total_pages_est=0):
        self.buffer = buffer
        self.progress_bar = progress_bar
        self.status_box = status_box
        self.total_pages_est = _progress_int(total_pages_est, 0)
        self._line_buffer = ""
        self._start_time = time.perf_counter()
        self._last_percent = 0

    def write(self, value):
        self.buffer.write(value)
        self._line_buffer += value
        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self._handle_line(line.strip())

    def flush(self):
        if self._line_buffer.strip():
            self._handle_line(self._line_buffer.strip())
            self._line_buffer = ""

    def update_status(self, msg):
        msg = _clean(msg)
        if not msg:
            return

        elapsed = self._elapsed_text()

        m = re.search(r"상세\s*URL\s*([\d,]+)건\s*수집\s*완료", msg)
        if m:
            total_urls = _progress_int(m.group(1), 0)
            self._set_progress(
                30,
                f"상세 URL {total_urls:,}건 수집 완료. 메타데이터 수집을 시작합니다... / 경과 {elapsed}",
            )
            return

        if "엑셀" in msg or "파일을 생성" in msg:
            self._set_progress(98, f"{msg} / 경과 {elapsed}")
            return

        if "상세 URL" in msg and "수집" in msg:
            self._set_progress(max(self._last_percent, 10), f"{msg} / 경과 {elapsed}")
            return

        if "상세 HTML" in msg or "메타데이터" in msg:
            self._set_progress(max(self._last_percent, 30), f"{msg} / 경과 {elapsed}")

    def _elapsed_text(self):
        elapsed = max(0, int(time.perf_counter() - self._start_time))
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _set_progress(self, percent, text):
        percent = max(0, min(100, int(percent)))
        self._last_percent = percent
        self.progress_bar.progress(percent, text=text)
        if self.status_box is not None:
            self.status_box.info(text)

    def _handle_line(self, line):
        if not line:
            return

        elapsed = self._elapsed_text()

        # 목록 URL 수집 시작: [LIST] page 01/100 수집 중
        m = re.search(r"\[LIST\]\s+page\s+(\d+)\s*/\s*(\d+)\s+수집\s+중", line)
        if m:
            page = _progress_int(m.group(1), 0)
            total = self.total_pages_est or _progress_int(m.group(2), 0)
            if total > 0:
                ratio = min(1.0, max(0.0, page / total))
                percent = 5 + int(ratio * 25)
                msg = f"상세 URL 목록 수집 중... {page:,}/{total:,}페이지 / 경과 {elapsed}"
            else:
                percent = max(self._last_percent, 8)
                msg = f"상세 URL 목록 수집 중... {page:,}페이지 확인 중 / 경과 {elapsed}"
            self._set_progress(min(percent, 30), msg)
            return

        # 목록 URL 누적: [LIST] page 01 +1000건 | 누적 1000/100000
        m = re.search(r"\[LIST\]\s+page\s+(\d+).*?\+\s*(\d+)건\s*\|\s*누적\s*([\d,]+)\s*/\s*([\d,]+)", line)
        if m:
            page = _progress_int(m.group(1), 0)
            added = _progress_int(m.group(2), 0)
            collected = _progress_int(m.group(3), 0)

            if self.total_pages_est > 0:
                ratio = min(1.0, max(0.0, page / self.total_pages_est))
                percent = 5 + int(ratio * 25)
            else:
                percent = max(self._last_percent, 10)

            msg = (
                f"상세 URL 목록 수집 중... {page:,}페이지 완료 / "
                f"이번 페이지 {added:,}건 / 누적 URL {collected:,}건 / 경과 {elapsed}"
            )
            self._set_progress(min(percent, 30), msg)
            return

        # 상세 URL 수집 완료: [상세 URL 수집 완료] 913건
        m = re.search(r"상세\s*URL\s*수집\s*완료.*?([\d,]+)건", line)
        if m:
            total_urls = _progress_int(m.group(1), 0)
            self._set_progress(
                30,
                f"상세 URL {total_urls:,}건 수집 완료. 메타데이터 수집을 시작합니다... / 경과 {elapsed}",
            )
            return

        # 상세 수집 진행: [DETAIL] 50/913 ( 5.5%) | 성공 50 | 실패 0 ...
        m = re.search(
            r"\[.*?DETAIL.*?\]\s*([\d,]+)\s*/\s*([\d,]+)\s*\(\s*([\d.]+)%\s*\).*?성공\s*([\d,]+)\s*\|\s*실패\s*([\d,]+)",
            line,
        )
        if m:
            done = _progress_int(m.group(1), 0)
            total = _progress_int(m.group(2), 0)
            ok = _progress_int(m.group(4), 0)
            fail = _progress_int(m.group(5), 0)

            ratio = min(1.0, max(0.0, done / total)) if total > 0 else 0.0
            percent = 30 + int(ratio * 65)
            msg = (
                f"상세 메타데이터 수집 중... {done:,}/{total:,}건 "
                f"({ratio * 100:.1f}%) / 성공 {ok:,} / 실패·보류 {fail:,} / 경과 {elapsed}"
            )
            self._set_progress(min(percent, 95), msg)
            return

        if "[후순위 처리]" in line or "[Playwright 최종 회수]" in line or "[Playwright 회수]" in line:
            self._set_progress(max(self._last_percent, 95), f"후순위 URL 및 Playwright 회수 처리 중... / 경과 {elapsed}")
            return

        if "수집 완료" in line or "전체 완료" in line:
            self._set_progress(max(self._last_percent, 96), f"수집 결과 정리 중... / 경과 {elapsed}")
            return



def _make_fail_row(item, seq, stage, error_text):
    return {
        "수집시각": crawler_metadata.now_str() if hasattr(crawler_metadata, "now_str") else time.strftime("%Y-%m-%d %H:%M:%S"),
        "단계": stage,
        "파일데이터명": _clean(item.get("title") or item.get("raw_title")),
        "URL": _clean(item.get("detail_url")),
        "최종순번": seq,
        "조회수": _clean(item.get("조회수")),
        "다운로드(바로가기)": _clean(item.get("다운로드(바로가기)") or item.get("다운로드수")),
        "오류": _clean(error_text),
        "Traceback": traceback.format_exc(),
    }


def _normalize_item_from_url(url: str):
    return {
        "raw_title": "",
        "title": "",
        "title_source": "direct_url",
        "확장자": "",
        "조회수": "",
        "다운로드(바로가기)": "",
        "다운로드수": "",
        "detail_url": url,
        "source_list_url": "",
    }


async def _fetch_and_parse_one(client, item, seq: int):
    """상세페이지 1건 수집. 성공 시 metadata, 실패 시 fail 반환."""
    url = _clean(item.get("detail_url"))
    if not url:
        return None, _make_fail_row(item, seq, "empty_url", "상세 URL이 비어 있습니다.")

    last_err = ""
    candidates = crawler_metadata.make_detail_url_candidates(url)

    for candidate_url in candidates:
        try:
            await asyncio.sleep(random.uniform(0.08, 0.25))
            res = await client.get(candidate_url)
            status = res.status_code
            if status in [403, 429, 500, 502, 503, 504]:
                raise RuntimeError(f"HTTP status={status}, url={candidate_url}")

            html = res.text or ""
            if len(html) < getattr(crawler_metadata, "SHORT_HTML_MIN_LEN", 500):
                raise RuntimeError(f"EMPTY_OR_SHORT_HTML status={status}, len={len(html)}, url={candidate_url}")

            # 대체 URL로 성공해도 최종 저장 URL은 원래 URL 유지
            parse_item = dict(item)
            parse_item["detail_url"] = url

            metadata, _column_rows = crawler_metadata.parse_detail_html(
                html=html,
                item=parse_item,
                final_seq=seq,
                source_file_label="기관별수집",
            )
            metadata["최종순번"] = seq
            return metadata, None

        except Exception as e:
            last_err = repr(e)
            continue

    return None, _make_fail_row(item, seq, "fetch_or_parse_detail", last_err or "unknown error")


async def _run_concurrent_metadata_scraping(items, progress_bar, HEADERS):
    queue = asyncio.Queue()
    for seq, item in enumerate(items, start=1):
        queue.put_nowait((seq, item))

    metadata_rows = []
    fail_rows = []
    total = len(items)
    completed = 0

    limits = httpx.Limits(max_connections=16, max_keepalive_connections=8)
    timeout = httpx.Timeout(connect=8.0, read=25.0, write=8.0, pool=8.0)
    concurrency = 10

    async with httpx.AsyncClient(
        headers=HEADERS,
        limits=limits,
        timeout=timeout,
        follow_redirects=True,
        verify=True,
    ) as client:
        async def worker():
            nonlocal completed
            while not queue.empty():
                try:
                    seq, item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                metadata, fail = await _fetch_and_parse_one(client, item, seq)
                if metadata is not None:
                    metadata_rows.append(metadata)
                if fail is not None:
                    fail_rows.append(fail)

                completed += 1
                progress_percent = 10 + int((completed / max(total, 1)) * 85)
                progress_bar.progress(
                    progress_percent,
                    text=f"상세페이지 파싱 중... ({completed}/{total} 완료, 실패 {len(fail_rows)}건)",
                )
                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        await asyncio.gather(*workers)

    metadata_rows = sorted(metadata_rows, key=lambda x: int(x.get("최종순번") or 0))
    fail_rows = sorted(fail_rows, key=lambda x: int(x.get("최종순번") or 0))
    return metadata_rows, fail_rows


def render_tab2(
    get_soup,
    find_valid_org_name,
    format_tel_no,
    BASE_URL,
    HEADERS,
    ALL_SELECTABLE_COLUMNS,
    TARGET_METADATA_KEYS,
    METADATA_KEY_MAP,
    collect_detail_urls_by_org=None,
    build_org_file_list_url=None,
    collect_detail_items_by_org=None,
):
    guide_html = """
    <div style="background-color: #F0F4F8; padding: 25px; border-radius: 12px; margin-bottom: 30px; border: 1px solid #E1E8F0;">
        <h4 style="margin-top: 0px; margin-bottom: 20px; color: #1E3A8A;">사용 방법</h4>
        <div style="display: flex; gap: 15px;">
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 1</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">검색창에 <b>제공기관명</b>을 입력하고 [검색]을 누릅니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 2</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;">추출을 원하는 <b>데이터 항목</b>을 선택합니다.</div>
            </div>
            <div style="flex: 1; background-color: white; padding: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.02);">
                <div style="font-weight: bold; color: #2563EB; margin-bottom: 8px; font-size: 15px;">STEP 3</div>
                <div style="font-size: 14px; color: #475569; line-height: 1.5;"><b>[추출]</b> 버튼을 누르고 완료되면 엑셀을 다운로드합니다.</div>
            </div>
        </div>
    </div>
    """
    st.markdown(guide_html, unsafe_allow_html=True)

    st.markdown("**▪&nbsp; 실행 전 필수 라이브러리 준비**")
    prep_col_btn, prep_col_msg = st.columns([1.4, 3.6])

    with prep_col_btn:
        prepare_clicked = st.button(
            "필수 라이브러리 설치/점검",
            icon=":material/build:",
            use_container_width=True,
            key="prepare_required_runtime_btn1",
        )

    with prep_col_msg:
        if st.session_state.get("required_runtime_ready1"):
            st.success("✅ 필수 실행 환경 준비 완료")
        else:
            st.caption("최초 실행 또는 배포 직후에는 먼저 설치/점검을 완료한 뒤 기관검색을 진행하세요.")

    if prepare_clicked:
        with st.spinner("Playwright Chromium 설치 및 실행 가능 여부를 확인 중입니다..."):
            ok, msg = _prepare_required_runtime_before_org_search()

        st.session_state.required_runtime_ready1 = bool(ok)

        if ok:
            st.success(f"✅ {msg}")
        else:
            st.error(f"❌ {msg}")

    if not st.session_state.get("required_runtime_ready1"):
        st.info("먼저 [필수 라이브러리 설치/점검] 버튼을 눌러 실행 환경을 준비해주세요.")
        return

    st.markdown("**▪&nbsp; 제공기관명 입력** (예: 한국중부발전(주), (재)한국저작권보호원)")
    col_input, col_btn = st.columns([4, 1])

    with col_input:
        org_input1 = st.text_input(
            "제공기관(기관별 메타데이터)",
            label_visibility="collapsed",
            placeholder="기관명을 입력하면 해당 기관의 메타데이터만 추출합니다.",
            key="org_input1",
        )

    with col_btn:
        search_clicked1 = st.button("검색", icon=":material/search:", use_container_width=True, key="search_btn1")

    if search_clicked1:
        if not org_input1.strip():
            st.warning("제공기관명을 입력해주세요!")
        else:
            with st.spinner(f"'{org_input1}' 검색 결과를 확인 중입니다..."):
                exact_org_name, total_pages = find_valid_org_name(org_input1.strip())
                st.session_state.total_pages1 = total_pages
                st.session_state.target_org1 = exact_org_name

            if total_pages == 0:
                st.error("❌ 검색 결과가 없습니다. 기관명을 다시 확인해주세요.")
            else:
                if exact_org_name != org_input1.strip():
                    st.info(f"💡 '{exact_org_name}'(으)로 자동 변환하여 검색했습니다.")
                st.success(f"✅ 검색 완료! 총 {total_pages}페이지의 파일데이터가 발견되었습니다.")

    if st.session_state.get("total_pages1", 0) <= 0:
        return

    st.markdown("---")
    st.markdown("**▪&nbsp; 추출할 항목 선택**")

    options_with_all = ["모두 선택"] + ALL_SELECTABLE_COLUMNS

    if "warning_msg1" not in st.session_state:
        st.session_state.warning_msg1 = ""
    if "selected_cols1" not in st.session_state:
        st.session_state.selected_cols1 = []
    if "prev_selected_cols1" not in st.session_state:
        st.session_state.prev_selected_cols1 = []

    def check_selection1():
        current = st.session_state.selected_cols1
        prev = st.session_state.prev_selected_cols1

        if "모두 선택" in current and len(current) > 1:
            if "모두 선택" in prev:
                st.session_state.selected_cols1 = ["모두 선택"]
                st.session_state.warning_msg1 = "⚠️ '모두 선택' 상태에서는 개별 항목을 추가할 수 없습니다."
            else:
                st.session_state.selected_cols1 = prev
                st.session_state.warning_msg1 = "⚠️ 개별 항목이 선택된 상태에서는 '모두 선택'을 추가할 수 없습니다."
        else:
            st.session_state.warning_msg1 = ""

        st.session_state.prev_selected_cols1 = st.session_state.selected_cols1

    col_multi, col_extract = st.columns([4, 1])

    with col_multi:
        selected_columns = st.multiselect(
            "항목 선택",
            options=options_with_all,
            placeholder="원하는 항목을 골라주세요",
            label_visibility="collapsed",
            key="selected_cols1",
            on_change=check_selection1,
        )
        if st.session_state.warning_msg1:
            st.warning(st.session_state.warning_msg1)

    with col_extract:
        run_extract = st.button("추출", type="primary", use_container_width=True, key="run_extract1")

    if not run_extract:
        return

    if not selected_columns:
        st.error("최소 1개 이상의 추출 항목을 선택해주세요!")
        return

    my_bar = st.progress(0, text="데이터 추출을 시작합니다...")

    try:
        org = st.session_state.target_org1
        pages = int(st.session_state.total_pages1 or 0)
        target_columns = ALL_SELECTABLE_COLUMNS if "모두 선택" in selected_columns else selected_columns

        list_per_page = 1000
        total_count_est = max(0, pages * 10)
        engine_max_pages = max(1, math.ceil(total_count_est / list_per_page) + 2) if total_count_est else 0

        if build_org_file_list_url is None:
            raise RuntimeError("기관별 FILE 목록 URL 생성 함수가 전달되지 않았습니다.")

        target_url = build_org_file_list_url(org, current_page=1, per_page=list_per_page)
        safe_org_name = org.replace("(", "_").replace(")", "")

        st.caption(f"수집 URL: {target_url}")
        my_bar.progress(5, text="crawler_metadata.py 기준 기관별 수집 엔진 준비 중...")

        log_box = st.empty()
        status_box = st.empty()
        log_buffer = io.StringIO()
        progress_stdout = _StreamlitProgressStdout(
            buffer=log_buffer,
            progress_bar=my_bar,
            status_box=status_box,
            total_pages_est=pages,
        )

        def update_status(msg):
            status_box.info(msg)
            progress_stdout.update_status(msg)

        with contextlib.redirect_stdout(progress_stdout):
            result = crawler_metadata.run_metadata_crawler_for_url(
                target_url=target_url,
                job_name=f"공공데이터_{safe_org_name}_기관별_메타데이터",
                max_pages=engine_max_pages,
                max_detail_items=0,
                list_per_page=list_per_page,
                detail_concurrency=10,
                source_file_label="기관별수집",
                output_dir=None,
                save_outputs_to_disk=False,
                headless=True,
                status_callback=update_status,
            )

        progress_stdout.flush()
        status_box.empty()
        log_text = log_buffer.getvalue()
        if log_text:
            log_box.text_area("수집 로그", log_text[-12000:], height=260)

        my_bar.progress(98, text="엑셀 파일 생성 중...")

        result_df = result.get("metadata_df", pd.DataFrame())
        fail_df = result.get("fail_df", pd.DataFrame())
        total_urls = len(result.get("items", []))

        if not result_df.empty and "최종순번" in result_df.columns:
            result_df = result_df.sort_values("최종순번", kind="stable")

        # --------------------------------------------------
        # 기관별 수집 결과의 제공기관 오염값만 최소 보정
        # - URL 수집/상세 HTML 수집/파싱 로직은 변경하지 않음
        # - 빈 값은 보정하지 않음
        # - 컬럼목록/설명 문구가 제공기관으로 잘못 들어간 경우만 검색 기관명으로 교정
        # --------------------------------------------------
        if not result_df.empty and "제공기관" in result_df.columns:
            expected_org = _clean(org)
            expected_org_norm = re.sub(r"\s+", "", expected_org)

            def _is_polluted_provider_value(value):
                v = _clean(value)

                # 빈 값은 상세 HTML 수집/파싱 실패 가능성이 있으므로 여기서 보정하지 않음
                if not v:
                    return False

                v_norm = re.sub(r"\s+", "", v)

                # 정상 기관명은 그대로 유지
                if v_norm == expected_org_norm:
                    return False

                # 컬럼목록/설명/데이터명 일부가 제공기관으로 잘못 들어간 패턴
                pollution_signals = [
                    ",",
                    "제공항목",
                    "항목명",
                    "항목설명",
                    "발간물명",
                    "발간주기",
                    "분야",
                    "컬럼",
                    "데이터",
                    "설명",
                    "기록하고",
                    "현황",
                ]

                if any(sig in v for sig in pollution_signals):
                    return True

                # 제공기관 값으로 보기 어려운 긴 문장형 값
                if len(v) > 80:
                    return True

                return False

            bad_provider_mask = result_df["제공기관"].apply(_is_polluted_provider_value)
            if bad_provider_mask.any():
                result_df.loc[bad_provider_mask, "제공기관"] = expected_org

        final_cols = [c for c in target_columns if c in result_df.columns]
        result_df = result_df[final_cols] if final_cols else pd.DataFrame(columns=target_columns)

        fail_cols = getattr(crawler_metadata, "FAIL_COLUMNS", ["수집시각", "단계", "파일데이터명", "URL", "최종순번", "조회수", "다운로드(바로가기)", "오류", "Traceback"])
        if fail_df.empty:
            fail_df = pd.DataFrame(columns=fail_cols)
        else:
            fail_df = fail_df.reindex(columns=fail_cols)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
            result_df.to_excel(writer, index=False, sheet_name="메타데이터")
            fail_df.to_excel(writer, index=False, sheet_name="실패로그")
        output.seek(0)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        file_name = f"공공데이터_{safe_org_name}_메타데이터_{timestamp}.xlsx"

        my_bar.empty()
        st.success(f"수집 완료! 상세 URL {total_urls}건 중 메타데이터 {len(result_df)}건, 실패 {len(fail_df)}건입니다.")
        if len(fail_df) > 0:
            st.warning("일부 상세페이지는 수집 실패했습니다. 다운로드한 엑셀의 [실패로그] 시트에서 URL과 오류 원인을 확인하세요.")
            preview_cols = [c for c in ["파일데이터명", "URL", "오류"] if c in fail_df.columns]
            if preview_cols:
                st.dataframe(fail_df[preview_cols], use_container_width=True)

        st.download_button(
            label="🌟 엑셀(Excel) 파일 다운로드",
            data=output,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    except Exception as e:
        my_bar.empty()
        st.error(f"🚨 오류 발생: {e}")
