# -*- coding: utf-8 -*-
"""
crawler_data_integrated.py

기존 crawler_data.py의 다운로드 진행 과정은 유지하되,
EXE 전용 Chromium 경로 의존성을 제거하고 Resolver가 검증한 URL을 바로 받을 수 있게 보강한 버전.

유지되는 흐름
- 목록 페이지 진입
- 목록 li 안정화 후 상세 URL/제목 수집
- 각 상세페이지 이동
- 현재데이터 다운로드
- 과거데이터 모달 열기
- CSV 우선, 없으면 첫 번째 다운로드 버튼 fallback
- 페이지네이션 이동
- 전체 폴더 ZIP 생성

보강된 부분
- org_url이 이미 파일데이터 목록이면 '파일데이터' 탭 클릭 생략
- 기관 전용 페이지이면 기존처럼 파일데이터 탭 클릭
- Playwright 기본 브라우저 사용 가능
- output_dir 지정 가능
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional, Iterable

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.data.go.kr"


def clean_title(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    return text or "unnamed"


def get_chromium_launch_kwargs(headless: bool = True, browser_executable_path: Optional[str] = None):
    kwargs = {"headless": headless}
    if browser_executable_path:
        if not os.path.exists(browser_executable_path):
            raise FileNotFoundError(f"브라우저 실행 파일을 찾을 수 없습니다: {browser_executable_path}")
        kwargs["executable_path"] = browser_executable_path
    return kwargs


def has_file_list(page) -> bool:
    try:
        return bool(page.query_selector_all("div.result-list ul li, #fileDataList ul li"))
    except Exception:
        return False


def ensure_filedata_list_page(page):
    """이미 파일데이터 목록이면 유지, 아니면 파일데이터 탭을 클릭한다."""
    if has_file_list(page):
        print("✅ 파일데이터 목록 화면 직접 진입 확인", flush=True)
        return

    print("📂 파일데이터 탭 진입 시도", flush=True)
    selectors = [
        "a.dtype-tab[data-type='FILE']",
        "li#dTypeFILE a",
        "a[href*='dType=FILE']",
        "a:has-text('파일데이터')",
        "button:has-text('파일데이터')",
    ]
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                page.evaluate("el => el.click()", el)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                time.sleep(1)
                if has_file_list(page):
                    print("✅ 파일데이터 탭 진입 완료", flush=True)
                    return
        except Exception:
            continue

    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=15000)


def load_items(page):
    page.wait_for_selector("div.result-list ul li, #fileDataList ul li")

    # 렌더링 지연 대응: li 개수 안정화
    last_count = -1
    stable_round = 0
    max_wait = 10

    for _ in range(max_wait):
        items = page.query_selector_all("div.result-list ul li, #fileDataList ul li")
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
    seen = set()
    for li in items:
        a = li.query_selector("a[href*='/data/'], a[href*='/dataset/']")
        if not a:
            continue

        title_el = li.query_selector("span.title, .title")
        raw_title = ""
        try:
            if title_el:
                raw_title = title_el.get_attribute("title") or title_el.inner_text()
            if not raw_title:
                raw_title = a.get_attribute("title") or a.inner_text()
        except Exception:
            raw_title = ""

        href = a.get_attribute("href") or ""
        if href.startswith("/"):
            href = BASE_URL + href
        if not href or href in seen:
            continue
        seen.add(href)

        datasets.append({"title": clean_title(raw_title), "href": href})

    return datasets


def make_page_signature(datasets: Iterable[dict]) -> tuple:
    """현재 목록 페이지가 이전 페이지와 같은지 판단하기 위한 안정적인 서명."""
    sig = []
    for d in datasets or []:
        href = str(d.get("href", "")).strip()
        title = str(d.get("title", "")).strip()
        if href or title:
            sig.append((href, title))
    return tuple(sig)


def get_first_item_key(page) -> str:
    """페이지 전환 검증용 첫 번째 데이터셋 키."""
    try:
        datasets = load_items(page)
        if not datasets:
            return ""
        d = datasets[0]
        return f"{d.get('href','')}|{d.get('title','')}"
    except Exception:
        return ""


def wait_until_page_changed(page, before_key: str, timeout_sec: float = 8.0) -> bool:
    """다음 페이지 클릭 후 첫 번째 항목이 바뀌었는지 확인한다."""
    start = time.time()
    while time.time() - start < timeout_sec:
        time.sleep(0.3)
        after_key = get_first_item_key(page)
        if after_key and after_key != before_key:
            return True
    return False


def goto_next(page) -> bool:
    """
    다음 페이지로 이동한다.

    기존 코드의 위험 지점:
    - 클릭은 성공했지만 실제 목록이 바뀌지 않아 같은 페이지가 반복될 수 있음
    - 다음 버튼이 비활성인데도 selector가 잡혀 True가 반환될 수 있음

    보강:
    - 클릭 전 첫 번째 항목 key 저장
    - 클릭 후 첫 번째 항목이 바뀌는 경우에만 True
    - 바뀌지 않으면 False로 처리해 while 루프 종료
    """
    before_key = get_first_item_key(page)

    try:
        curr = page.query_selector("nav.pagination strong.active, .pagination strong.active, nav.pagination strong")
        if curr:
            next_el = curr.evaluate_handle("node => node.nextElementSibling")
            if next_el:
                tag = next_el.get_property("tagName").json_value().lower()
                if tag == "a":
                    cls = next_el.get_property("className").json_value() or ""
                    if "disabled" not in cls and "off" not in cls:
                        page.evaluate("el => el.click()", next_el)
                        try:
                            page.wait_for_load_state("networkidle", timeout=10000)
                        except Exception:
                            pass
                        if wait_until_page_changed(page, before_key):
                            return True
                        print("📌 다음 페이지 클릭 후 목록 변화 없음 → 종료", flush=True)
                        return False
    except Exception:
        pass

    for sel in ["a.control.next", "a[title*='다음']", "a:has-text('다음')"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                cls = el.get_attribute("class") or ""
                aria_disabled = el.get_attribute("aria-disabled") or ""
                if "disabled" in cls or "off" in cls or aria_disabled.lower() == "true":
                    continue
                page.evaluate("el => el.click()", el)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                if wait_until_page_changed(page, before_key):
                    return True
                print("📌 다음 버튼 클릭 후 목록 변화 없음 → 종료", flush=True)
                return False
        except Exception:
            continue
    return False


def main(
    inst_name: str,
    org_url: str,
    output_dir: str | None = None,
    headless: bool = True,
    browser_executable_path: str | None = None,
    max_pages: int = 0,
) -> str:
    inst_name = clean_title(inst_name)
    if not org_url:
        raise ValueError("org_url이 비어 있습니다.")

    root_base = Path(output_dir or ".").resolve()
    root_base.mkdir(parents=True, exist_ok=True)
    ROOT_DIR = root_base / f"{inst_name}_포털데이터"
    ROOT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        launch_kwargs = get_chromium_launch_kwargs(
            headless=headless,
            browser_executable_path=browser_executable_path,
        )
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            accept_downloads=True,
            locale="ko-KR",
            viewport={"width": 1440, "height": 960},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.goto(org_url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        time.sleep(2)

        print(f"🏢 {inst_name} 기관 파일데이터 페이지 접속 완료", flush=True)
        print(f"🔗 진입 URL: {org_url}", flush=True)

        ensure_filedata_list_page(page)

        page_num = 1
        visited_page_signatures = set()
        processed_detail_urls = set()

        while True:
            if max_pages and page_num > max_pages:
                print(f"\n📌 MAX_PAGES={max_pages} 도달 → 종료", flush=True)
                break
            print("\n============================", flush=True)
            print(f"📄 페이지 {page_num} 처리 시작", flush=True)
            print("============================", flush=True)

            datasets = load_items(page)
            print(f"📑 {len(datasets)}개 데이터셋 발견", flush=True)

            page_signature = make_page_signature(datasets)
            if page_signature in visited_page_signatures:
                print("📌 이미 처리한 목록 페이지가 다시 나타남 → 무한 반복 방지를 위해 종료", flush=True)
                break
            visited_page_signatures.add(page_signature)

            if not datasets:
                print("⚠ 현재 페이지에서 데이터셋을 찾지 못했습니다. 종료합니다.", flush=True)
                break

            list_page_url = page.url

            for idx, d in enumerate(datasets, start=1):
                title = d["title"]
                href = d["href"]

                if href in processed_detail_urls:
                    print(f"\n⏭ 이미 처리한 상세 URL 건너뜀: {title}", flush=True)
                    continue
                processed_detail_urls.add(href)

                print(f"\n📂 [{idx}] {title}", flush=True)
                print(f"🔗 {href}", flush=True)

                save_dir = ROOT_DIR / title
                save_dir.mkdir(parents=True, exist_ok=True)

                past_dir = save_dir / "과거데이터"
                past_dir.mkdir(parents=True, exist_ok=True)

                # 상세 페이지 이동
                try:
                    page.goto(href, wait_until="domcontentloaded", timeout=30000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    time.sleep(0.4)
                except Exception as e:
                    print(f"   ⚠ 상세페이지 이동 실패: {e}", flush=True)
                    continue

                # 현재데이터 다운로드
                try:
                    with page.expect_download(timeout=40000) as dl_info:
                        # 기존 코드와 동일하게 '다운로드' 텍스트 우선
                        page.click("a:has-text('다운로드')")
                    dl = dl_info.value
                    original = clean_title(dl.suggested_filename)
                    dl.save_as(str(save_dir / original))
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
                        if not modal:
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
                            page.evaluate("el => el.click()", target_btn)
                        file = d2.value

                        original = clean_title(file.suggested_filename)
                        base, ext = os.path.splitext(original)
                        new_name = f"{base}(과거{j}){ext}"
                        file.save_as(str(past_dir / new_name))
                        print(f"   ✅ 과거데이터[{j}] 저장됨 → {new_name}", flush=True)

                        close = page.query_selector("#layer_data_infomation button.close")
                        if close:
                            close.click()

                except Exception as e:
                    print("   ⚠ 과거데이터 오류:", e, flush=True)

                # 목록으로 복귀
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=20000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    time.sleep(0.4)
                    if not has_file_list(page):
                        page.goto(list_page_url, wait_until="domcontentloaded", timeout=30000)
                        try:
                            page.wait_for_load_state("networkidle", timeout=8000)
                        except Exception:
                            pass
                    ensure_filedata_list_page(page)
                except Exception as e:
                    print(f"   ⚠ 목록 복귀 실패: {e}", flush=True)
                    page.goto(list_page_url, wait_until="domcontentloaded", timeout=30000)
                    ensure_filedata_list_page(page)

            if not goto_next(page):
                print("\n📌 다음 페이지 없음 → 종료", flush=True)
                break

            page_num += 1

        print("\n🎉 전체 다운로드 완료!", flush=True)
        context.close()
        browser.close()

    zip_path = shutil.make_archive(str(ROOT_DIR), "zip", str(ROOT_DIR))
    print(f"[ZIP 생성] {zip_path}", flush=True)
    return zip_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="공공데이터포털 기관별 파일데이터 다운로드")
    parser.add_argument("--inst-name", required=True)
    parser.add_argument("--org-url", required=True)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--headless", default="true", choices=["true", "false"])
    parser.add_argument("--browser-executable-path", default="")
    parser.add_argument("--max-pages", default="0")
    args = parser.parse_args()

    main(
        inst_name=args.inst_name,
        org_url=args.org_url,
        output_dir=args.output_dir,
        headless=args.headless.lower() == "true",
        browser_executable_path=args.browser_executable_path or None,
        max_pages=int(args.max_pages or 0),
    )
