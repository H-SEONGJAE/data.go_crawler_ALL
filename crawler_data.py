import os
import re
import time
import shutil
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


def get_chromium_launch_kwargs(headless=True, browser_executable_path=None):
    """
    Playwright 브라우저 실행 옵션 생성.
    Streamlit Cloud/Linux에서는 packages.txt로 설치된 시스템 Chromium을 우선 사용한다.
    """
    import shutil as _shutil

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
        _shutil.which("chromium") or "",
        _shutil.which("chromium-browser") or "",
        _shutil.which("google-chrome") or "",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            kwargs["executable_path"] = path
            print(f"[Chromium] 시스템 브라우저 사용: {path}", flush=True)
            return kwargs

    print("[Chromium] 시스템 브라우저를 찾지 못해 Playwright 기본 브라우저를 사용합니다.", flush=True)
    return kwargs


def clean_title(text):
    text = (text or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text or "unnamed"


def normalize_list_url(url, current_page=1, per_page=1000):
    """
    기관별 목록 URL을 currentPage/perPage 직접 순회용으로 정규화한다.
    - perPage=1000으로 최대한 크게 요청해 페이지 누락 가능성을 줄인다.
    - 기존 org/orgFullName/orgFilter 등 기관 조건은 보존한다.
    """
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["dType"] = "FILE"
    query["sort"] = query.get("sort") or "updtDt"
    query["currentPage"] = str(current_page)
    query["perPage"] = str(per_page)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def load_items(page):
    """현재 목록 페이지의 파일데이터 항목을 안정적으로 읽는다. 항목이 없으면 [] 반환."""
    try:
        page.wait_for_selector("div.result-list ul li", timeout=15000)
    except PlaywrightTimeoutError:
        return []

    last_count = -1
    stable_round = 0
    for _ in range(15):  # 최대 1.5초 안정화
        items = page.query_selector_all("div.result-list ul li")
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
    for li in page.query_selector_all("div.result-list ul li"):
        a = li.query_selector("a[href*='/data/'], a[href*='/dataset/']")
        if not a:
            continue

        href = a.get_attribute("href") or ""
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.data.go.kr" + href

        # 상세 URL만 사용
        if "fileData.do" not in href and not re.search(r"/(?:data|dataset)/\d+", href):
            continue

        title_el = li.query_selector("span.title") or li.query_selector(".title")
        raw_title = title_el.inner_text().strip() if title_el else a.inner_text().strip()
        datasets.append({"title": clean_title(raw_title), "href": href})

    return datasets


def main(inst_name, org_url, headless=True, browser_executable_path=None, per_page=1000, max_pages=1000):
    """
    기관별 파일데이터 다운로드.

    기존 다운로드 로직은 유지하되, 페이지 이동은 기존 nextElementSibling 클릭 방식 대신
    currentPage 직접 순회 방식으로 변경한다. 이로써 JSHandle null 오류와 페이지 그룹 누락을 방지한다.
    """

    def run_crawler(inst_name, org_url):
        root_dir = f"{inst_name}_포털데이터"
        os.makedirs(root_dir, exist_ok=True)
        seen_detail_urls = set()

        with sync_playwright() as p:
            launch_kwargs = get_chromium_launch_kwargs(headless=headless, browser_executable_path=browser_executable_path)
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()

            try:
                page_num = 1
                first_url = normalize_list_url(org_url, current_page=page_num, per_page=per_page)
                print(f"🏢 {inst_name} 기관별 전용 페이지 접속", flush=True)
                print(f"🔗 목록 URL: {first_url}", flush=True)
                page.goto(first_url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                time.sleep(1.5)

                while page_num <= max_pages:
                    print("\n============================", flush=True)
                    print(f"📄 페이지 {page_num} 처리 시작", flush=True)
                    print("============================", flush=True)

                    datasets = load_items(page)
                    print(f"📑 {len(datasets)}개 데이터셋 발견", flush=True)

                    if not datasets:
                        print("📌 현재 페이지에 데이터셋이 없어 종료", flush=True)
                        break

                    new_datasets = []
                    for d in datasets:
                        href = d.get("href", "")
                        if href in seen_detail_urls:
                            continue
                        seen_detail_urls.add(href)
                        new_datasets.append(d)

                    if not new_datasets:
                        print("📌 신규 데이터셋이 없어 종료", flush=True)
                        break

                    for idx, d in enumerate(new_datasets, start=1):
                        title = d["title"]
                        href = d["href"]

                        print(f"\n📂 [{idx}] {title}", flush=True)
                        print(f"🔗 {href}", flush=True)

                        save_dir = os.path.join(root_dir, title)
                        os.makedirs(save_dir, exist_ok=True)

                        past_dir = os.path.join(save_dir, "과거데이터")
                        os.makedirs(past_dir, exist_ok=True)

                        # 상세 페이지 이동
                        try:
                            page.goto(href, wait_until="domcontentloaded", timeout=60000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                pass
                            time.sleep(0.4)
                        except Exception as e:
                            print("   ⚠ 상세페이지 이동 실패:", e, flush=True)
                            # 목록 페이지로 복귀 후 다음 항목 진행
                            page.goto(normalize_list_url(org_url, current_page=page_num, per_page=per_page), wait_until="domcontentloaded", timeout=60000)
                            continue

                        # 현재데이터 다운로드
                        try:
                            with page.expect_download(timeout=40000) as dl_info:
                                page.click("a:has-text('다운로드')")
                            dl = dl_info.value
                            original = dl.suggested_filename
                            dl.save_as(os.path.join(save_dir, original))
                            print(f"   ✅ 현재데이터 저장됨 → {original}", flush=True)
                        except Exception as e:
                            print("   ⚠ 현재데이터 실패:", e, flush=True)

                        # 과거데이터 다운로드
                        try:
                            links = page.query_selector_all("a[onclick*='fileDataDetail']")
                            print(f"📂 과거데이터 {len(links)}건", flush=True)

                            for j, el in enumerate(links, start=1):
                                onclick = el.get_attribute("onclick")
                                if not onclick:
                                    continue
                                page.evaluate(onclick)

                                page.wait_for_function(
                                    """
                                    ()=> {
                                        const m=document.querySelector('#layer_data_infomation .file-meta-table-mobile');
                                        return m && window.getComputedStyle(m).display==='block';
                                    }
                                    """,
                                    timeout=7000,
                                )

                                modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
                                if modal is None:
                                    print("   ⚠ 과거데이터 모달 없음 → 패스", flush=True)
                                    continue

                                csv_btns = modal.query_selector_all("a.button.white:has-text('CSV')")
                                if csv_btns:
                                    target_btn = csv_btns[-1]
                                else:
                                    fallback = modal.query_selector_all("a.button.white")
                                    if not fallback:
                                        print("   ⚠ 다운로드 버튼 없음 → 패스", flush=True)
                                        close = page.query_selector("#layer_data_infomation button.close")
                                        if close:
                                            close.click()
                                        continue
                                    target_btn = fallback[0]
                                    print("   → CSV 없음 → 첫 번째 버튼 사용", flush=True)

                                with page.expect_download(timeout=60000) as d2:
                                    page.evaluate("(el)=>el.click()", target_btn)
                                file = d2.value

                                original = file.suggested_filename
                                base, ext = os.path.splitext(original)
                                new_name = f"{base}(과거{j}){ext}"
                                file.save_as(os.path.join(past_dir, new_name))
                                print(f"   ✅ 과거데이터[{j}] 저장됨 → {new_name}", flush=True)

                                close = page.query_selector("#layer_data_infomation button.close")
                                if close:
                                    close.click()
                        except Exception as e:
                            print("   ⚠ 과거데이터 오류:", e, flush=True)

                        # 목록 페이지로 명시 복귀. browser history 의존을 줄인다.
                        try:
                            page.goto(normalize_list_url(org_url, current_page=page_num, per_page=per_page), wait_until="domcontentloaded", timeout=60000)
                            try:
                                page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                pass
                            time.sleep(0.3)
                        except Exception as e:
                            print("   ⚠ 목록 복귀 실패:", e, flush=True)

                    # 다음 페이지 직접 이동. 기존 goto_next(nextElementSibling) 오류 제거.
                    if len(datasets) < per_page:
                        print(f"\n📌 현재 페이지 반환 건수({len(datasets)})가 perPage({per_page})보다 작아 종료", flush=True)
                        break

                    page_num += 1
                    next_url = normalize_list_url(org_url, current_page=page_num, per_page=per_page)
                    print(f"\n➡️ 다음 페이지 이동: {next_url}", flush=True)
                    page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=15000)
                    except Exception:
                        pass
                    time.sleep(1.0)

                print("\n🎉 전체 다운로드 완료!", flush=True)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        zip_path = shutil.make_archive(root_dir, "zip", root_dir)
        return zip_path

    return run_crawler(inst_name, org_url)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="공공데이터포털 기관별 파일데이터 다운로드")
    parser.add_argument("--inst-name", required=True, help="기관명")
    parser.add_argument("--org-url", required=True, help="기관별 파일데이터 페이지 URL")
    parser.add_argument("--headless", default="true", choices=["true", "false"], help="브라우저 headless 실행 여부")
    parser.add_argument("--per-page", type=int, default=1000, help="목록 페이지당 요청 건수")
    parser.add_argument("--max-pages", type=int, default=1000, help="최대 순회 페이지")
    args = parser.parse_args()

    main(
        args.inst_name,
        args.org_url,
        headless=(args.headless.lower() == "true"),
        per_page=args.per_page,
        max_pages=args.max_pages,
    )
