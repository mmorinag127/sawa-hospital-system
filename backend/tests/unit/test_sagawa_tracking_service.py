import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT))

from src.services.sagawa_tracking_service import _extract_tracking_events  # noqa: E402


def test_extract_tracking_events_parses_sagawa_history_table():
    html = """
    <html>
      <body>
        <table class="table_okurijo_detail2">
          <tr>
            <th>お問い合せ送り状No.</th>
            <td>4906-4011-5910</td>
          </tr>
          <tr>
            <th>出荷日</th>
            <td>2026年03月24日</td>
          </tr>
        </table>
        <table class="table_okurijo_detail2">
          <tr>
            <th>荷物状況</th>
            <th>日時</th>
            <th>担当営業所</th>
          </tr>
          <tr>
            <td>↓集荷</td>
            <td>03/24 14:17</td>
            <td>徳島営業所</td>
          </tr>
          <tr>
            <td>⇒輸送中</td>
            <td>03/24 17:01</td>
            <td>四国中継センター</td>
          </tr>
        </table>
      </body>
    </html>
    """

    events = _extract_tracking_events(html, looked_up_at=datetime(2026, 3, 25, 8, 0, 0))

    assert len(events) == 2
    assert events[0].event_status == "↓集荷"
    assert events[0].event_at_text == "03/24 14:17"
    assert events[0].office_name == "徳島営業所"
    assert events[1].event_status == "⇒輸送中"
    assert events[1].event_at_text == "03/24 17:01"
    assert events[1].office_name == "四国中継センター"
    assert events[1].event_at.isoformat() == "2026-03-24T17:01:00"
