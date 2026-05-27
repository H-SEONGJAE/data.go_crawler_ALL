# -*- coding: utf-8 -*-
"""확정된 상세 URL 목록에서 목록 카드 기반 조회수/다운로드수 엑셀 저장."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict

import pandas as pd


def _safe_name(value: str) -> str:
    value = str(value or "기관").strip()
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    return value or "기관"


def run_stats_from_resolution(resolution: Dict, output_root: str = "outputs") -> Dict:
    items = resolution.get("detail_items", [])
    if not items:
        raise RuntimeError("확정된 상세 URL 목록이 없습니다. 먼저 기관 검색 · URL 수집을 완료하세요.")

    rows = []
    for row in items:
        rows.append({
            "데이터명": row.get("title", ""),
            "제공기관": row.get("provider_name", resolution.get("selected_provider", "")),
            "조회수": row.get("view_count", ""),
            "다운로드수": row.get("download_count", ""),
            "상세페이지 URL": row.get("detail_url", ""),
            "목록 출처": row.get("source_list_url", ""),
            "검색어": row.get("source_keyword", ""),
            "제공기관 유사도 점수": row.get("provider_score", ""),
        })
    df = pd.DataFrame(rows)

    provider = _safe_name(resolution.get("selected_provider", "기관"))
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_root) / f"{provider}_조회다운로드수_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"공공데이터_{provider}_조회수_다운로드수_{ts}.xlsx"

    with pd.ExcelWriter(out_path, engine="xlsxwriter", engine_kwargs={"options": {"strings_to_urls": False}}) as writer:
        df.to_excel(writer, index=False, sheet_name="FILE_집계")

    return {"output_dir": str(out_dir), "path": str(out_path), "row_count": len(df)}
