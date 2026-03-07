import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services import order_service  # noqa: E402


def test_build_llm_assist_prompt_includes_structured_tables_and_issue_context():
    prompt = order_service._build_llm_assist_prompt(
        provider="openai",
        template={},
        llm_assist=True,
        pipeline_output={
            "table_raw": "|日付|区分|献立|常食|\n|-|-|-|-|\n|2/15|朝|Menu A|12|",
            "tables": [
                {
                    "table_id": "p1_t1",
                    "page_index": 1,
                    "row_count": 4,
                    "col_count": 4,
                    "rows": [
                        ["日付", "区分", "献立", "常食"],
                        ["2/15", "朝", "Menu A", "12"],
                    ],
                    "cells": [
                        {
                            "row_index": 1,
                            "col_index": 3,
                            "row_span": 2,
                            "col_span": 1,
                            "text": "48",
                            "bbox": [0.1, 0.2, 0.2, 0.3],
                        }
                    ],
                }
            ],
            "yomitoku_cell_issues": [
                {
                    "table_id": "p1_t1",
                    "source_row_index": 0,
                    "column_index": 3,
                    "issue_code": "merged_numeric_cell",
                    "severity": "high",
                    "bbox": [0.1, 0.2, 0.2, 0.3],
                    "text": "48",
                    "source": "yomitoku_structured",
                }
            ],
        },
    )

    assert prompt is not None
    assert "Second-pass repair mode" in prompt
    assert "structured tables/cells" in prompt
    assert "merged_numeric_cell" in prompt
    assert "\"bbox\": [0.1, 0.2, 0.2, 0.3]" in prompt
