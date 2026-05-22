# -*- coding: utf-8 -*-
"""공공데이터포털 크롤러 공통 유틸."""
from __future__ import annotations

import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.data.go.kr"
LIST_URL = f"{BASE_URL}/tcs/dss/selectDataSetList.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def clean_text(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def clean_filename(value: str, fallback: str = "unnamed") -> str:
    text = clean_text(value)
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or fallback


def build_file_list_url(
    org_name: str | None = None,
    *,
    current_page: int = 1,
    per_page: int = 10,
    keyword: str = "",
    sort: str = "updtDt",
) -> str:
    """
    공공데이터포털 파일데이터 목록 URL 생성.

    - org_name이 비어 있으면 전체 파일데이터 대상 URL을 만든다.
    - org_name이 있으면 포털의 제공기관 필터 계열 파라미터를 함께 채운다.
    """
    org = clean_text(org_name)
    params = {
        "dType": "FILE",
        "keyword": clean_text(keyword),
        "detailKeyword": "",
        "publicDataPk": "",
        "recmSe": "N",
        "detailText": "",
        "relatedKeyword": "",
        "commaNotInData": "",
        "commaAndData": "",
        "commaOrData": "",
        "must_not": "",
        "tabId": "",
        "dataSetCoreTf": "",
        "coreDataNm": "",
        "sort": sort,
        "relRadio": "",
        "orgFullName": org,
        "orgFilter": org,
        "org": org,
        "orgSearch": org,
        "currentPage": str(int(current_page or 1)),
        "perPage": str(int(per_page or 10)),
        "brm": "",
        "instt": "",
        "svcType": "",
        "kwrdArray": "",
        "extsn": "",
        "coreDataNmArray": "",
        "operator": "AND",
        "pblonsipScopeCode": "PBDE07",
    }
    return LIST_URL + "?" + urllib.parse.urlencode(params)


def make_org_candidates(user_input: str) -> list[str]:
    """기관명 오입력/표기 차이를 줄이기 위한 최소 후보 생성."""
    base = clean_text(user_input)
    if not base:
        return []
    candidates = [base]
    if "(주)" not in base and "㈜" not in base:
        candidates.extend([base + "(주)", base + "㈜"])
    else:
        candidates.extend([base.replace("(주)", "㈜"), base.replace("㈜", "(주)")])
    if "강원특별자치도" in base:
        candidates.append(base.replace("강원특별자치도", "강원도"))
    if "강원도" in base:
        candidates.append(base.replace("강원도", "강원특별자치도"))
    return list(dict.fromkeys([c for c in candidates if c]))


def quick_check_org(org_name: str, timeout: int = 8) -> tuple[bool, str]:
    """기관명 후보가 포털 1페이지에서 목록을 반환하는지 가볍게 확인."""
    url = build_file_list_url(org_name, current_page=1, per_page=10)
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "lxml")
        selectors = (
            "div.result-list ul li, #fileDataList ul li, "
            "a[href*='/data/'][href*='fileData.do'], "
            "a[href*='/dataset/'][href*='fileData.do']"
        )
        return bool(soup.select(selectors)), url
    except Exception:
        return False, url


def resolve_org_name(user_input: str) -> tuple[str, str, bool]:
    """
    기관명 후보를 확인하고 실제 실행에 사용할 기관명과 URL을 반환.
    실패해도 원 입력값으로 URL을 만들어 실행 가능하게 둔다.
    """
    candidates = make_org_candidates(user_input)
    last_url = ""
    for cand in candidates:
        ok, url = quick_check_org(cand)
        last_url = url
        if ok:
            return cand, url, True
    base = clean_text(user_input)
    return base, last_url or build_file_list_url(base), False


def build_chromium_launch_kwargs(headless: bool = True, browser_executable_path: str | None = None) -> dict:
    """
    로컬/Streamlit Cloud/GitHub 배포 환경에서 Chromium 실행 경로를 보정.
    browser_executable_path가 있으면 우선 사용하고, 없으면 시스템 Chromium 후보를 찾는다.
    """
    kwargs = {"headless": bool(headless)}
    candidates: list[str] = []
    if browser_executable_path:
        candidates.append(browser_executable_path)
    candidates.extend([
        os.environ.get("CHROMIUM_EXECUTABLE_PATH", ""),
        os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", ""),
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        shutil.which("google-chrome") or "",
        shutil.which("google-chrome-stable") or "",
    ])
    for path in candidates:
        if path and Path(path).exists():
            kwargs["executable_path"] = path
            return kwargs
    return kwargs


def file_size_label(path: str | Path) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    size = p.stat().st_size
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
