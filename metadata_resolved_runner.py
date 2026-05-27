# -*- coding: utf-8 -*-
"""확정된 상세 URL 목록을 기존 crawler_metadata.py 상세 수집 엔진에 연결."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Dict, List

import crawler_metadata as cm


def _safe_name(value: str) -> str:
    value = str(value or "기관").strip()
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    return value or "기관"


def build_cm_items(resolution: Dict) -> List[Dict]:
    items = []
    for row in resolution.get("detail_items", []):
        title = row.get("title", "")
        detail_url = row.get("detail_url", "")
        if not detail_url:
            continue
        items.append({
            "raw_title": title,
            "title": title,
            "title_source": "resolved_detail_url",
            "확장자": "",
            "조회수": row.get("view_count", ""),
            "다운로드(바로가기)": row.get("download_count", ""),
            "다운로드수": row.get("download_count", ""),
            "detail_url": detail_url,
            "source_list_url": row.get("source_list_url", ""),
        })
    return items


async def _run_async(resolution: Dict, output_root: str = "outputs", concurrency: int = 8) -> Dict:
    items = build_cm_items(resolution)
    if not items:
        raise RuntimeError("확정된 상세 URL 목록이 없습니다. 먼저 기관 검색 · URL 수집을 완료하세요.")

    provider = _safe_name(resolution.get("selected_provider", "기관"))
    ts = time.strftime("%Y%m%d_%H%M%S")
    output_dir = Path(output_root) / f"{provider}_메타데이터_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 기존 crawler_metadata.py의 상세 수집 엔진 재사용
    results = await cm.collect_details_httpx_concurrent(
        items=items,
        source_file_label="기관URL수집목록",
        concurrency=max(1, int(concurrency)),
        output_dir=str(output_dir),
        defer_block=True,
    )

    saved = cm.save_outputs(
        output_dir=str(output_dir),
        metadata_rows=results.get("metadata_rows", []),
        column_rows=results.get("column_rows", []),
        fail_rows=results.get("fail_rows", []),
    )

    return {
        "output_dir": str(output_dir),
        "success_count": len(results.get("metadata_rows", [])),
        "fail_count": len(results.get("fail_rows", [])),
        "paths": saved.get("paths", {}),
    }


def run_metadata_from_resolution(resolution: Dict, output_root: str = "outputs", concurrency: int = 8) -> Dict:
    return asyncio.run(_run_async(resolution, output_root=output_root, concurrency=concurrency))
