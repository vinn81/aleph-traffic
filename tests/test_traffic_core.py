import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from traffic_core import TrafficDataError, summarize_traffic

KST = ZoneInfo("Asia/Seoul")


class SummarizeTrafficTests(unittest.TestCase):
    def test_filters_road_and_invalid_speed(self):
        payload = {
            "body": {
                "items": [
                    {"roadName": "달구벌대로", "linkId": "A", "speed": "30", "createdDate": "20260907085500"},
                    {"roadName": "달구벌대로", "linkId": "B", "speed": "0", "createdDate": "20260907085500"},
                    {"roadName": "다른도로", "linkId": "C", "speed": "50", "createdDate": "20260907085500"},
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
        self.assertEqual(result["samples"][0]["linkId"], "A")

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

    def test_keeps_at_most_five_evenly_spaced_real_samples(self):
        items = [
            {
                "roadName": "달구벌대로",
                "linkId": f"L{i}",
                "speed": 20 + i,
                "createdDate": "20260907085500",
            }
            for i in range(10)
        ]
        result = summarize_traffic(
            {"items": items},
            captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
        )
        self.assertEqual(len(result["samples"]), 5)
        self.assertEqual(result["samples"][0]["linkId"], "L0")
        self.assertEqual(result["samples"][-1]["linkId"], "L9")

    def test_empty_data_has_stable_failure_type(self):
        with self.assertRaises(TrafficDataError) as ctx:
            summarize_traffic(
                {"items": []},
                captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
            )
        self.assertEqual(ctx.exception.failure_type, "EMPTY_DATA")

    def test_api_error_has_http_failure_type(self):
        with self.assertRaises(TrafficDataError) as ctx:
            summarize_traffic(
                {"header": {"resultCode": "99", "resultMsg": "error"}},
                captured_at=datetime(2026, 9, 7, 9, 0, tzinfo=KST),
            )
        self.assertEqual(ctx.exception.failure_type, "HTTP_ERROR")


if __name__ == "__main__":
    unittest.main()

class OfficialCollectionWindowTests(unittest.TestCase):
    def test_official_collection_window(self):
        from collect_local import in_official_collection_window
        from datetime import datetime
        from zoneinfo import ZoneInfo

        kst = ZoneInfo("Asia/Seoul")
        self.assertTrue(in_official_collection_window(datetime(2026, 9, 8, 9, 0, tzinfo=kst)))
        self.assertTrue(in_official_collection_window(datetime(2026, 9, 8, 9, 9, 59, tzinfo=kst)))
        self.assertFalse(in_official_collection_window(datetime(2026, 9, 8, 9, 10, tzinfo=kst)))
        self.assertFalse(in_official_collection_window(datetime(2026, 9, 8, 11, 0, tzinfo=kst)))
