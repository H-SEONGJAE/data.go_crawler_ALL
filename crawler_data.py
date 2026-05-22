import re
import time
import shutil
from playwright.sync_api import sync_playwright
import sys
import os

try:
    from org_url_resolver import title_prefix_matches_input
except Exception:
    title_prefix_matches_input = None

def get_chromium_launch_kwargs(headless=True, browser_executable_path=None):
    """
    Playwright 브라우저 실행 옵션을 생성합니다.
    - Streamlit Cloud/Linux: packages.txt로 설치된 시스템 Chromium을 우선 사용
    - 로컬: Playwright 기본 Chromium 사용
    - EXE 방식은 제거했으므로 sys.executable 기준 번들 경로는 사용하지 않음
    """
    import shutil

    kwargs = {"headless": headless}
    if browser_executable_path and os.path.exists(browser_executable_path):
        kwargs["executable_path"] = browser_executable_path
        print(f"[Chromium] 지정 브라우저 사용: {browser_executable_path}", flush=True)
        return kwargs

    candidates = [
        os.environ.get("CHROMIUM_EXECUTABLE_PATH", ""),
        os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("google-chrome") or "",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            kwargs["executable_path"] = path
            print(f"[Chromium] 시스템 브라우저 사용: {path}", flush=True)
            return kwargs

    print("[Chromium] 시스템 브라우저를 찾지 못해 Playwright 기본 브라우저를 사용합니다.", flush=True)
    return kwargs

def main(inst_name, org_url, headless=True, browser_executable_path=None, title_prefix_filter=""):
    def clean_title(text):
        text = text.strip()
        text = re.sub(r"[\\/:*?\"<>|]", "_", text)
        text = re.sub(r"\s+", " ", text)
        return text
    
    
    # 🔽 load_items – 렌더링 지연/URL 형태 혼재 대응 버전
    def load_items(page):
        list_selector = "div.result-list ul li, #fileDataList ul li, ul.result-list li, ul.data-list li"
        page.wait_for_selector(list_selector, timeout=15000)

        # 🔽 li 개수 안정화 (최대 1초)
        last_count = -1
        stable_round = 0
        max_wait = 10  # 0.1초 × 10 = 1초

        items = []
        for _ in range(max_wait):
            items = page.query_selector_all(list_selector)
            count = len(items)

            if count == last_count:
                stable_round += 1
                if stable_round >= 3:
                    break
            else:
                stable_round = 0

            last_count = count
            time.sleep(0.1)

        datasets = []
        for li in items:
            a = li.query_selector("a[href*='/data/'][href*='fileData.do'], a[href*='/dataset/'][href*='fileData.do'], a[href*='/data/'], a[href*='/dataset/']")
            if not a:
                continue

            title_el = li.query_selector("span.title, .title")
            raw_title = title_el.inner_text().strip() if title_el else a.inner_text().strip()
            raw_title = re.sub(r"\s+", " ", raw_title).replace("미리보기", "").strip()

            if title_prefix_filter:
                if title_prefix_matches_input is None:
                    if title_prefix_filter.replace(" ", "") not in raw_title.replace(" ", ""):
                        continue
                else:
                    try:
                        if not title_prefix_matches_input(raw_title, title_prefix_filter):
                            continue
                    except Exception:
                        continue

            href = a.get_attribute("href") or ""
            if not href:
                continue
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
            
            launch_kwargs = get_chromium_launch_kwargs(headless=headless, browser_executable_path=browser_executable_path)
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(org_url, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle")
            time.sleep(3)
    
            print(f"🏢 {inst_name} 기관별 전용 페이지 접속 완료")
            print(f"🔎 목록명 prefix 필터: {title_prefix_filter or '없음'}")
    
            page.wait_for_selector("a:has-text('파일데이터')")
            page.click("a:has-text('파일데이터')")
            page.wait_for_selector("div.result-list ul li, #fileDataList ul li, ul.result-list li, ul.data-list li")
    
            page_num = 1
    
    
            while True:
                print("\n============================")
                print(f"📄 페이지 {page_num} 처리 시작")
                print("============================")
    
                # 🔽 안정화된 목록 가져오기
                datasets = load_items(page)
                print(f"📑 {len(datasets)}개 데이터셋 발견")
    
                # 🔽 데이터셋 순회
                for idx, d in enumerate(datasets, start=1):
                    title = d["title"]
                    href = d["href"]
    
                    print(f"\n📂 [{idx}] {title}")
                    print(f"🔗 {href}")
    
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
                        print(f"   ✅ 현재데이터 저장됨 → {original}")
                    except Exception as e:
                        print("   ⚠ 현재데이터 실패:", e)
    
                    # 🔽 과거데이터 다운로드
                    try:
                        links = page.query_selector_all("a[onclick*='fileDataDetail']")
                        print(f"📂 과거데이터 {len(links)}건")
    
                        for j, el in enumerate(links, start=1):
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
                                    print("   ⚠ 다운로드 버튼 없음 → 패스")
                                    close = page.query_selector("#layer_data_infomation button.close")
                                    if close:
                                        close.click()
                                    continue
                                target_btn = fallback[0]
                                print("   → CSV 없음 → 첫 번째 버튼 사용")
    
                            # 🔽 다운로드
                            with page.expect_download(timeout=60000) as d2:
                                page.evaluate("(el)=>el.click()", target_btn)
                            file = d2.value
    
                            original = file.suggested_filename
                            base, ext = os.path.splitext(original)
                            new_name = f"{base}(과거{j}){ext}"
    
                            file.save_as(os.path.join(past_dir, new_name))
                            print(f"   ✅ 과거데이터[{j}] 저장됨 → {new_name}")
    
                            # 🔽 모달 닫기
                            close = page.query_selector("#layer_data_infomation button.close")
                            if close:
                                close.click()
    
                    except Exception as e:
                        print("   ⚠ 과거데이터 오류:", e)
    
                    # 🔽 목록으로 복귀
                    page.go_back()
                    page.wait_for_load_state("networkidle")
                    time.sleep(0.4)
    
                # 🔽 다음 페이지 이동
                if not goto_next(page):
                    print("\n📌 다음 페이지 없음 → 종료")
                    break
    
                page_num += 1
    
            print("\n🎉 전체 다운로드 완료!")
            browser.close()
            
            zip_path = shutil.make_archive(ROOT_DIR, "zip", ROOT_DIR)
            return zip_path
    return run_crawler(inst_name, org_url)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="공공데이터포털 기관별 파일데이터 다운로드")
    parser.add_argument("--inst-name", required=True, help="기관명")
    parser.add_argument("--org-url", required=True, help="기관별 파일데이터 페이지 URL")
    parser.add_argument("--headless", default="true", choices=["true", "false"], help="브라우저 headless 실행 여부")
    args = parser.parse_args()

    main(
        args.inst_name,
        args.org_url,
        headless=(args.headless.lower() == "true"),
    )
