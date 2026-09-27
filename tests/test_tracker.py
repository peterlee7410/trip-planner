"""tracker.py 的離線測試：python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tracker  # noqa: E402

CFG = {**tracker.DEFAULT_CONFIG, "origin": "TPE", "destination": "KMQ",
       "depart_date": "2026-11-21", "return_date": "2026-11-22", "adults": 2}


def test_round_trip_query_names_the_airport():
    # 「Flights to KMQ from TPE」會被 Google 解析成昆明 KMG；加上 airport 才會是小松
    rt = tracker.urls(CFG)["rt"]
    assert "KMQ%20airport" in rt


def test_one_page_failing_keeps_the_others():
    def load(key):
        if key == "rt":
            raise TimeoutError("rt 頁逾時")
        return [f"{key}-label"]

    got = tracker.collect(load)
    assert got == {"out": ["out-label"], "ret": ["ret-label"], "rt": []}


def test_all_pages_failing_raises():
    def load(key):
        raise TimeoutError(key)

    try:
        tracker.collect(load)
    except SystemExit as e:
        assert "Google Flights" in str(e)
    else:
        raise AssertionError("應該要中止")


def test_round_trip_has_plain_fallback():
    u = tracker.urls(CFG)
    assert "airport" not in u["rt_alt"] and "KMQ%20from%20TPE" in u["rt_alt"]


def test_header_check_rejects_wrong_airport():
    assert tracker.header_ok("航班搜尋 來回 2 經濟艙 臺北市 TPE 小松市 KMQ 篩選器", "TPE", "KMQ")
    assert not tracker.header_ok("航班搜尋 來回 2 經濟艙 臺北市 昆明市 KMG 篩選器", "TPE", "KMQ")
    assert tracker.header_ok("航班搜尋 單程 2 經濟艙 臺北市 小松市 KMQ 篩選器", "TPE", "KMQ")
