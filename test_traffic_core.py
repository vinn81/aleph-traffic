import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from traffic_core import summarize_traffic

KST = ZoneInfo("Asia/Seoul")


class SummarizeTrafficTests(unittest.TestCase):
    def test_filters_road_and_invalid_speed(self):
        payload = {
            "body": {
                "items": [
                    {"roadName": "달구벌대로", "speed": "30", "createdDate": "20260907085500"},
                    {"roadName": "달구벌대로", "speed": "0", "createdDate": "20260907085500"},
                    {"roadName": "다른도로", "speed": "50", "createdDate": "20260907085500"},
                ]
            }
        }
        result = summarize_traffic(
            payload,
            captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
        )
        self.assertEqual(result["linkCount"], 1)
        self.assertEqual(result["averageSpeed"], 30.0)
        self.assertEqual(result["sourceStatus"], "NORMAL")

    def test_stale_ratio_marks_delayed(self):
        payload = {
            "items": [
                {"roadName": "달구벌대로", "speed": 20, "createdDate": "20260907080000"},
                {"roadName": "달구벌대로", "speed": 30, "createdDate": "20260907080500"},
                {"roadName": "달구벌대로", "speed": 40, "createdDate": "20260907085500"},
            ]
        }
        result = summarize_traffic(
            payload,
            captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
        )
        self.assertEqual(result["staleLinkCount"], 2)
        self.assertAlmostEqual(result["staleRatio"], 2 / 3, places=4)
        self.assertEqual(result["sourceStatus"], "DELAYED")

    def test_one_fresh_link_does_not_hide_many_stale_links(self):
        items = [
            {"roadName": "달구벌대로", "speed": 25, "createdDate": "20260907080000"}
            for _ in range(9)
        ]
        items.append(
            {"roadName": "달구벌대로", "speed": 35, "createdDate": "20260907085900"}
        )
        result = summarize_traffic(
            {"items": items},
            captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
        )
        self.assertEqual(result["sourceStatus"], "DELAYED")
        self.assertEqual(result["staleLinkCount"], 9)
        self.assertEqual(result["sourceTimestampCount"], 10)


if __name__ == "__main__":
    unittest.main()
