# -*- coding: utf-8 -*-
"""기관별 파일데이터 최신/과거 다운로드 크롤러. EXE 의존성을 제거한 Streamlit/CLI 공용 버전."""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from portal_common import build_chromium_launch_kwargs, build_file_list_url, clean_filename, clean_text


def clean_title(text: str) -> str:
    text = clean_text(text)
    # 앞쪽 형식 배지 제거
    text = re.sub(r"^((CSV|JSON|XML|XLSX|XLS|PDF|HWPX|HWP|TXT|ZIP|SHP)\s*(\+|,|/|-)?\s*)+", "", text, flags=re.I)
    return clean_filename(text, fallback="unnamed_dataset")


def _abs_url(href: str) -> str:
    href = clean_text(href)
    if href.startswith("/"):
        return "https://www.data.go.kr" + href
    return href


def _list_page_url(base_url: str, page_no: int, per_page: int) -> str:
    # crawler_metadata의 optimize_list_url이 있으면 사용하고, 실패하면 공통 URL 생성 방식을 따른다.
    try:
        import crawler_metadata as cm
        return cm.optimize_list_url(base_url, per_page=per_page, current_page=page_no)
    except Exception:
        return base_url


def load_items(page) -> list[dict]:
    page.wait_for_selector("div.result-list ul li, #fileDataList ul li", timeout=15000)
    last_count = -1
    stable_round = 0
    for _ in range(15):
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
        href = _abs_url(a.get_attribute("href") or "")
        if "fileData.do" not in href and "/data/" not in href and "/dataset/" not in href:
            continue
        title_el = li.query_selector("span.title, .title")
        raw_title = title_el.inner_text().strip() if title_el else a.inner_text().strip()
        title = clean_title(raw_title.replace("미리보기", ""))
        key = href or title
        if key in seen:
            continue
        seen.add(key)
        datasets.append({"title": title, "href": href})
    return datasets


def _click_first_download(page, save_dir: Path) -> tuple[bool, str]:
    candidates = [
        "a:has-text('다운로드')",
        "button:has-text('다운로드')",
        "a.button:has-text('다운로드')",
    ]
    for selector in candidates:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            with page.expect_download(timeout=60000) as dl_info:
                loc.click()
            dl = dl_info.value
            filename = clean_filename(dl.suggested_filename, fallback="current_file")
            dl.save_as(str(save_dir / filename))
            return True, filename
        except Exception:
            continue
    return False, "다운로드 버튼을 찾지 못했습니다."


def _download_past_files(page, past_dir: Path) -> list[str]:
    saved = []
    links = page.query_selector_all("a[onclick*='fileDataDetail']")
    print(f"   📂 과거데이터 {len(links)}건", flush=True)
    for j, el in enumerate(links, start=1):
        try:
            onclick = el.get_attribute("onclick") or ""
            if not onclick:
                continue
            page.evaluate(onclick)
            page.wait_for_function(
                """
                () => {
                    const m = document.querySelector('#layer_data_infomation .file-meta-table-mobile');
                    return m && window.getComputedStyle(m).display === 'block';
                }
                """,
                timeout=10000,
            )
            modal = page.query_selector("#layer_data_infomation .file-meta-table-mobile")
            if not modal:
                continue
            csv_btns = modal.query_selector_all("a.button.white:has-text('CSV')")
            if csv_btns:
                target_btn = csv_btns[-1]
            else:
                fallback = modal.query_selector_all("a.button.white, a:has-text('다운로드')")
                if not fallback:
                    print("   ⚠ 과거데이터 다운로드 버튼 없음", flush=True)
                    close = page.query_selector("#layer_data_infomation button.close")
                    if close:
                        close.click()
                    continue
                target_btn = fallback[0]

            with page.expect_download(timeout=60000) as d2:
                page.evaluate("(el)=>el.click()", target_btn)
            file = d2.value
            original = clean_filename(file.suggested_filename, fallback=f"past_{j}")
            base, ext = os.path.splitext(original)
            new_name = f"{base}(과거{j}){ext}"
            file.save_as(str(past_dir / new_name))
            saved.append(new_name)
            print(f"   ✅ 과거데이터[{j}] 저장됨 → {new_name}", flush=True)
        except Exception as e:
            print(f"   ⚠ 과거데이터[{j}] 실패: {e}", flush=True)
        finally:
            try:
                close = page.query_selector("#layer_data_infomation button.close")
                if close:
                    close.click()
            except Exception:
                pass
    return saved


def main(
    inst_name: str,
    org_url: str | None = None,
    *,
    headless: bool = True,
    browser_executable_path: str | None = None,
    output_root: str | Path = ".",
    max_pages: int = 0,
    per_page: int = 100,
    auto_shutdown: bool = False,
) -> str:
    """
    기관명만으로 최신/과거 파일데이터 다운로드를 수행한다.

    max_pages=0이면 빈 페이지가 나올 때까지 진행한다.
    auto_shutdown=True는 CLI 단독 실행에서 수집 종료 후 프로세스를 명시 종료한다.
    """
    inst_name = clean_text(inst_name)
    if not inst_name:
        raise ValueError("inst_name이 비어 있습니다.")
    org_url = org_url or build_file_list_url(inst_name, current_page=1, per_page=per_page)

    root_dir = Path(output_root) / f"{clean_filename(inst_name)}_포털데이터"
    root_dir.mkdir(parents=True, exist_ok=True)
    print("=" * 80, flush=True)
    print("[기관별 파일데이터 최신/과거 다운로드]", flush=True)
    print(f"- 기관명: {inst_name}", flush=True)
    print(f"- URL: {org_url}", flush=True)
    print(f"- 저장폴더: {root_dir}", flush=True)
    print("=" * 80, flush=True)

    browser = None
    context = None
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(**build_chromium_launch_kwargs(headless, browser_executable_path))
            context = browser.new_context(accept_downloads=True, locale="ko-KR")
            page = context.new_page()

            page_no = 1
            while True:
                if max_pages and page_no > max_pages:
                    print("📌 최대 페이지 도달 → 종료", flush=True)
                    break
                list_url = _list_page_url(org_url, page_no, per_page)
                print("\n" + "=" * 60, flush=True)
                print(f"📄 목록 페이지 {page_no} 처리 시작", flush=True)
                print(list_url, flush=True)
                print("=" * 60, flush=True)

                try:
                    page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_load_state("networkidle", timeout=30000)
                except PlaywrightTimeoutError:
                    print("⚠ 목록 페이지 로딩 대기 시간 초과. 현재 DOM 기준으로 계속 진행합니다.", flush=True)

                try:
                    datasets = load_items(page)
                except Exception as e:
                    print(f"📌 목록 없음 또는 로딩 실패 → 종료: {e}", flush=True)
                    break

                if not datasets:
                    print("📌 더 이상 데이터셋이 없습니다 → 종료", flush=True)
                    break

                print(f"📑 {len(datasets)}개 데이터셋 발견", flush=True)
                for idx, d in enumerate(datasets, start=1):
                    title = d["title"]
                    href = d["href"]
                    print(f"\n📂 [{page_no}-{idx}] {title}", flush=True)
                    print(f"🔗 {href}", flush=True)

                    save_dir = root_dir / title
                    past_dir = save_dir / "과거데이터"
                    save_dir.mkdir(parents=True, exist_ok=True)
                    past_dir.mkdir(parents=True, exist_ok=True)

                    try:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_load_state("networkidle", timeout=30000)
                    except PlaywrightTimeoutError:
                        print("   ⚠ 상세페이지 로딩 대기 시간 초과. 현재 DOM 기준으로 계속 진행합니다.", flush=True)

                    ok, msg = _click_first_download(page, save_dir)
                    if ok:
                        print(f"   ✅ 현재데이터 저장됨 → {msg}", flush=True)
                    else:
                        print(f"   ⚠ 현재데이터 실패: {msg}", flush=True)

                    _download_past_files(page, past_dir)

                page_no += 1

        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass

    zip_path = shutil.make_archive(str(root_dir), "zip", str(root_dir))
    print(f"\n🎉 전체 다운로드 완료: {zip_path}", flush=True)
    if auto_shutdown:
        sys.exit(0)
    return zip_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="공공데이터포털 기관별 파일데이터 최신/과거 다운로드")
    parser.add_argument("--inst-name", required=True)
    parser.add_argument("--org-url", default="")
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--output-root", default=".")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--per-page", type=int, default=100)
    parser.add_argument("--auto-shutdown", choices=["true", "false"], default="true")
    args = parser.parse_args()

    main(
        args.inst_name,
        args.org_url or None,
        headless=args.headless.lower() == "true",
        output_root=args.output_root,
        max_pages=args.max_pages,
        per_page=args.per_page,
        auto_shutdown=args.auto_shutdown.lower() == "true",
    )
