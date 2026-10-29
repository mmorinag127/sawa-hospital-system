from src.services.ocr_audit_contract import (
    normalize_issue_code, parse_audit_confidence, parse_audit_int, parse_issues,
)


def test_integer_and_confidence_boundaries():
    assert [parse_audit_int(value) for value in (None, '', '1.5', '+1', '-2', ' 3 ')] == [None, None, None, None, -2, 3]
    assert [parse_audit_confidence(value) for value in (None, '', 'bad', '-1', '2', '.12345')] == [0, 0, 0, 0, 1, .12345]
    assert normalize_issue_code(' Column-Swap ') == 'column_swap'


def test_issue_parsing_keeps_original_evidence_and_explicit_positions():
    fields = ['issue_code', 'severity', 'row_index', 'column_index', 'confidence', 'evidence', 'reason']
    result = parse_issues(fields=fields, rows=[
        ['column-swap', 'HIGH', '0', '-1', '.12345', '  0 / 空欄  ', ' 根拠 '],
        ['new-code', '', '', '', '', '', ''],
        ['', '', '', '', '', '', 'unknown reason'],
        ['new-code', '', '', '', '', 'source evidence', ''],
    ])
    assert result == [
        {'issue_code': 'column_swap', 'severity': 'high', 'row_index': 0, 'column_index': -1,
         'confidence': .1235, 'evidence': '0 / 空欄', 'reason': '根拠'},
        {'issue_code': 'unknown_issue', 'severity': 'medium', 'confidence': 0, 'evidence': '', 'reason': 'unknown reason'},
        {'issue_code': 'new_code', 'severity': 'medium', 'confidence': 0, 'evidence': 'source evidence', 'reason': ''},
    ]
    assert parse_issues(rows=[['column_swap']], fields=[]) == []
