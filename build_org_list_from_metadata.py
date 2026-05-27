# -*- coding: utf-8 -*-
"""
build_org_list_from_metadata.py

메타데이터.xlsx의 '제공기관' 컬럼에서 org_list.json을 생성합니다.

사용 예시:
    python build_org_list_from_metadata.py
    python build_org_list_from_metadata.py "공공데이터포털_메타데이터_포털데이터/메타데이터.xlsx"
    python build_org_list_from_metadata.py "메타데이터.xlsx" "org_list.json"
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


def find_default_metadata_path() -> Path:
    candidates = [
        Path("메타데이터.xlsx"),
        Path("공공데이터포털_메타데이터_포털데이터") / "메타데이터.xlsx",
    ]
    candidates.extend(Path(".").glob("*_포털데이터/메타데이터.xlsx"))

    for path in candidates:
        if path.exists():
            return path

    raise FileNotFoundError(
        "메타데이터.xlsx를 찾지 못했습니다. 인자로 메타데이터 파일 경로를 넣어주세요."
    )


def build_org_list(metadata_path: Path, output_path: Path) -> None:
    df = pd.read_excel(metadata_path)
    if "제공기관" not in df.columns:
        raise KeyError("메타데이터 파일에 '제공기관' 컬럼이 없습니다.")

    orgs = (
        df["제공기관"]
        .dropna()
        .astype(str)
        .map(lambda x: " ".join(x.replace("\xa0", " ").split()))
    )
    orgs = sorted([x for x in orgs.drop_duplicates().tolist() if x])

    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": str(metadata_path),
        "count": len(orgs),
        "orgs": orgs,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"생성 완료: {output_path} / 제공기관 {len(orgs):,}개")


if __name__ == "__main__":
    metadata_path = Path(sys.argv[1]) if len(sys.argv) >= 2 else find_default_metadata_path()
    output_path = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path("org_list.json")
    build_org_list(metadata_path, output_path)
