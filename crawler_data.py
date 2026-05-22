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
        """
        목록 페이지의 다음 페이지로 이동합니다.

        v6 수정 이유:
        - 기존 코드는 active strong의 nextElementSibling을 JSHandle로 받은 뒤 tagName을 읽었습니다.
        - 마지막 페이지 또는 DOM이 순간적으로 바뀐 경우 nextElementSibling이 null이어도 JSHandle 객체는 생성되어
          JSHandle.get_property("tagName") 단계에서 오류가 발생했습니다.
        - 여기서는 JSHandle을 쓰지 않고, 브라우저 안에서 다음 숫자 페이지/다음 그룹 버튼 존재 여부를
          한 번에 판단하고 클릭합니다. 다음 페이지가 없으면 정상적으로 False를 반환합니다.
        """
        list_selector = "div.result-list ul li, #fileDataList ul li, ul.result-list li, ul.data-list li"

        try:
            page.wait_for_selector("nav.pagination", timeout=5000)
        except Exception:
            return False

        try:
            prev_first_title = ""
            first = page.query_selector(f"{list_selector} a")
            if first:
                prev_first_title = re.sub(r"\s+", " ", first.inner_text()).strip()
        except Exception:
            prev_first_title = ""

        try:
            clicked = page.evaluate(r"""
                () => {
                    const nav = document.querySelector('nav.pagination');
                    if (!nav) return {clicked:false, reason:'no pagination'};

                    const active = nav.querySelector('strong.active');
                    if (!active) return {clicked:false, reason:'no active'};

                    const children = Array.from(nav.children).filter(el => el && el.nodeType === 1);
                    const activeIdx = children.indexOf(active);

                    // 1) 현재 active 바로 뒤쪽의 숫자 페이지 링크 우선 클릭
                    if (activeIdx >= 0) {
                        for (let i = activeIdx + 1; i < children.length; i++) {
                            const el = children[i];
                            const tag = (el.tagName || '').toLowerCase();
                            const txt = (el.textContent || '').trim();
                            if (tag === 'a' && /^\d+$/.test(txt)) {
                                el.click();
                                return {clicked:true, kind:'number', page:txt};
                            }
                        }
                    }

                    // 2) 다음 숫자 링크가 없으면 다음 페이지 그룹 버튼 시도
                    const nextControls = Array.from(nav.querySelectorAll('a.control.next, a.next, a[title*="다음"]'));
                    for (const el of nextControls) {
                        const cls = el.className || '';
                        const ariaDisabled = el.getAttribute('aria-disabled');
                        if (cls.includes('disabled') || ariaDisabled === 'true') continue;
                        el.click();
                        return {clicked:true, kind:'control_next', page:''};
                    }

                    return {clicked:false, reason:'no next link'};
                }
            """)
        except Exception as e:
            print(f"   ⚠ 다음 페이지 판단 실패 → 종료: {e}")
            return False

        if not clicked or not clicked.get("clicked"):
            return False

        # AJAX/일반 navigation 모두 대응합니다. networkidle이 안 잡혀도 실패로 보지 않습니다.
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        try:
            page.wait_for_selector(list_selector, timeout=15000)
        except Exception as e:
            print(f"   ⚠ 다음 페이지 목록 로딩 실패 → 종료: {e}")
            return False

        # 동일 DOM이 잠깐 남는 경우를 대비해 짧게 안정화합니다.
        if prev_first_title:
            try:
                page.wait_for_function(
                    r"""
                    ([selector, prev]) => {
                        const a = document.querySelector(selector + ' a');
                        if (!a) return false;
                        const now = (a.innerText || '').replace(/\s+/g, ' ').trim();
                        return now && now !== prev;
                    }
                    """,
                    [list_selector, prev_first_title],
                    timeout=5000,
                )
            except Exception:
                # 마지막 그룹 이동/동일 첫 제목 케이스가 있을 수 있으므로 종료하지 않습니다.
                pass

        time.sleep(0.7)
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
