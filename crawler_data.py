# -*- coding: utf-8 -*-
"""
Streamlit 직접 호출용 기관별 공공데이터 파일데이터 다운로드 엔진.

- 기존 EXE/config.json 실행 방식 제거
- 기존 추출 결과가 달라지지 않도록 핵심 수집 로직과 selector는 유지
- Streamlit 진행상태 표시를 위한 status_callback만 추가
"""

import os
import re
import time
import shutil
from playwright.sync_api import sync_playwright

def main(inst_name, org_url, status_callback=None, stop_event=None, headless=True, browser_executable_path=None):
    """
    Streamlit에서 직접 호출하는 기관별 파일데이터 다운로드 엔진입니다.

    기존 crawler_data.py의 추출 로직(목록 수집, 현재데이터 다운로드, 과거데이터 다운로드,
    폴더 구조, ZIP 생성)은 유지하고, EXE/config.json 실행 방식만 제거했습니다.

    Parameters
    ----------
    inst_name : str
        저장 폴더명에 사용할 기관명. 기존과 동일하게 "{inst_name}_포털데이터" 폴더를 생성합니다.
    org_url : str
        공공데이터포털 기관별 파일데이터 페이지 URL.
    status_callback : callable, optional
        Streamlit 화면 진행 메시지 표시용 콜백. 예: lambda msg: st.info(msg)
    headless : bool
        Playwright headless 실행 여부. 기존과 동일하게 기본값 True입니다.
    browser_executable_path : str, optional
        별도 Chromium/Chrome 실행 파일 경로가 필요한 경우에만 지정합니다.

    Returns
    -------
    str
        생성된 ZIP 파일 경로.
    """

    def log(*args, current=None, total=None, level="info"):
        msg = " ".join(str(a) for a in args)
        print(msg)
        if status_callback:
            try:
                status_callback(msg, current=current, total=total, level=level)
            except TypeError:
                try:
                    status_callback(msg)
                except Exception:
                    pass
            except Exception:
                pass

    def should_stop():
        return bool(stop_event and stop_event.is_set())

    def find_system_chromium():
        """Streamlit Cloud/Linux에서 apt로 설치된 Chromium을 보조 탐색합니다."""
        candidates = [
            os.environ.get("CHROME_BIN", ""),
            os.environ.get("CHROMIUM_PATH", ""),
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    def launch_browser(p):
        launch_kwargs = {"headless": headless}
        if browser_executable_path:
            launch_kwargs["executable_path"] = browser_executable_path
            return p.chromium.launch(**launch_kwargs)

        try:
            return p.chromium.launch(**launch_kwargs)
        except Exception as first_error:
            system_chromium = find_system_chromium()
            if not system_chromium:
                raise first_error
            log(f"[브라우저] Playwright 기본 Chromium 실행 실패 → 시스템 Chromium 사용: {system_chromium}")
            launch_kwargs["executable_path"] = system_chromium
            return p.chromium.launch(**launch_kwargs)

    def clean_title(text):
        text = text.strip()
        text = re.sub(r"[\\/:*?\"<>|]", "_", text)
        text = re.sub(r"\s+", " ", text)
        return text
    
    
    # 🔽 load_items – 렌더링 지연 문제 해결 버전
    def load_items(page):
        page.wait_for_selector("div.result-list ul li")
    
        # 🔽 li 개수 안정화 (최대 1초)
        last_count = -1
        stable_round = 0
        max_wait = 10  # 0.1초 × 10 = 1초
    
        for _ in range(max_wait):
            items = page.query_selector_all("div.result-list ul li")
            count = len(items)
    
            if count == last_count:
                stable_round += 1
                if stable_round >= 3:  # 3번 연속 동일 → 안정됨
                    break
            else:
                stable_round = 0
    
            last_count = count
            time.sleep(0.1)
    
        # 🔽 안정된 items 리스트 처리
        datasets = []
        for li in items:
            a = li.query_selector("a[href*='/data/']")
            if not a:
                continue
    
            title_el = li.query_selector("span.title")
            raw_title = title_el.inner_text().strip() if title_el else a.inner_text().strip()
    
            href = a.get_attribute("href")
            if href.startswith("/"):
                href = "https://www.data.go.kr" + href
    
            datasets.append({"title": clean_title(raw_title), "href": href})
    
        return datasets
    
    
    # 🔽 페이지 네비게이션
    def goto_next(page):
        page.wait_for_selector("nav.pagination strong.active")
    
        curr = page.query_selector("nav.pagination strong.active")
        if not curr:
            return False
    
        next_el = curr.evaluate_handle("node => node.nextElementSibling")
        if not next_el:
            return False
    
        tag = next_el.get_property("tagName").json_value().lower()
        if tag != "a":
            return False
    
        page.evaluate("(el)=>el.click()", next_el)
        page.wait_for_load_state("networkidle")
        time.sleep(1)
        return True
    
    
    # 🔽 MAIN
    def run_crawler(inst_name, org_url):
        ROOT_DIR = f"{inst_name}_포털데이터"
        os.makedirs(ROOT_DIR, exist_ok=True)

        with sync_playwright() as p:
            browser = launch_browser(p)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            if should_stop():
                log("⏹ 중지 요청 감지: 브라우저 실행 전 종료", level="warning")
                return ""

            page.goto(org_url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            time.sleep(3)
    
            log(f"🏢 {inst_name} 기관별 전용 페이지 접속 완료")
    
            page.wait_for_selector("a:has-text('파일데이터')")
            page.click("a:has-text('파일데이터')")
            page.wait_for_selector("div.result-list ul li")
    
            page_num = 1
    
    
            while True:
                if should_stop():
                    log("⏹ 중지 요청 감지: 페이지 순회 중단", level="warning")
                    break

                log("\n============================")
                log(f"📄 페이지 {page_num} 처리 시작")
                log("============================")
    
                # 🔽 안정화된 목록 가져오기
                datasets = load_items(page)
                log(f"📑 {len(datasets)}개 데이터셋 발견")
    
                # 🔽 데이터셋 순회
                total_datasets = len(datasets)
                for idx, d in enumerate(datasets, start=1):
                    if should_stop():
                        log("⏹ 중지 요청 감지: 데이터셋 순회 중단", current=idx-1, total=total_datasets, level="warning")
                        break
                    title = d["title"]
                    href = d["href"]
    
                    log(f"\n📂 [{idx}] {title}", current=idx, total=total_datasets)
                    log(f"🔗 {href}")
    
                    save_dir = os.path.join(ROOT_DIR, title)
                    os.makedirs(save_dir, exist_ok=True)
    
                    past_dir = os.path.join(save_dir, "과거데이터")
                    os.makedirs(past_dir, exist_ok=True)
    
                    # 상세 페이지 이동
                    page.goto(href)
                    page.wait_for_load_state("networkidle")
                    time.sleep(0.4)
    
                    # 🔽 현재데이터 다운로드
                    try:
                        with page.expect_download(timeout=40000) as dl_info:
                            page.click("a:has-text('다운로드')")
                        dl = dl_info.value
                        original = dl.suggested_filename
                        dl.save_as(os.path.join(save_dir, original))
                        log(f"   ✅ 현재데이터 저장됨 → {original}")
                    except Exception as e:
                        log("   ⚠ 현재데이터 실패:", e)
    
                    # 🔽 과거데이터 다운로드
                    try:
                        links = page.query_selector_all("a[onclick*='fileDataDetail']")
                        log(f"📂 과거데이터 {len(links)}건")
    
                        for j, el in enumerate(links, start=1):
                            if should_stop():
                                log("⏹ 중지 요청 감지: 과거데이터 다운로드 중단", level="warning")
                                break
                            onclick = el.get_attribute("onclick")
                            page.evaluate(onclick)
    
                            # 🔽 모달 로딩 대기
                            page.wait_for_function("""
                                ()=> {
                                    const m=document.querySelector('#layer_data_infomation .file-meta-table-mobile');
                                    return m && window.getComputedStyle(m).display==='block';
                                }
                            """, timeout=7000)
    
                            modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
    
                            # 🔽 CSV 버튼 우선
                            csv_btns = modal.query_selector_all("a.button.white:has-text('CSV')")
    
                            if csv_btns:
                                target_btn = csv_btns[-1]
                            else:
                                # 🔽 CSV 없음 → 첫 번째 버튼 fallback
                                fallback = modal.query_selector_all("a.button.white")
                                if not fallback:
                                    log("   ⚠ 다운로드 버튼 없음 → 패스")
                                    close = page.query_selector("#layer_data_infomation button.close")
                                    if close:
                                        close.click()
                                    continue
                                target_btn = fallback[0]
                                log("   → CSV 없음 → 첫 번째 버튼 사용")
    
                            # 🔽 다운로드
                            with page.expect_download(timeout=60000) as d2:
                                page.evaluate("(el)=>el.click()", target_btn)
                            file = d2.value
    
                            original = file.suggested_filename
                            base, ext = os.path.splitext(original)
                            new_name = f"{base}(과거{j}){ext}"
    
                            file.save_as(os.path.join(past_dir, new_name))
                            log(f"   ✅ 과거데이터[{j}] 저장됨 → {new_name}")
    
                            # 🔽 모달 닫기
                            close = page.query_selector("#layer_data_infomation button.close")
                            if close:
                                close.click()
    
                    except Exception as e:
                        log("   ⚠ 과거데이터 오류:", e)
    
                    if should_stop():
                        break

                    # 🔽 목록으로 복귀
                    page.go_back()
                    page.wait_for_load_state("networkidle")
                    time.sleep(0.4)
    
                if should_stop():
                    break

                # 🔽 다음 페이지 이동
                if not goto_next(page):
                    log("\n📌 다음 페이지 없음 → 종료")
                    break
    
                page_num += 1
    
            if should_stop():
                log("\n⏹ 사용자 요청으로 다운로드를 중지했습니다. 현재까지 저장된 파일만 ZIP으로 묶습니다.", level="warning")
            else:
                log("\n🎉 전체 다운로드 완료!", level="success")
            browser.close()
            
            zip_path = shutil.make_archive(ROOT_DIR, "zip", ROOT_DIR)
            return zip_path
    return run_crawler(inst_name, org_url)
