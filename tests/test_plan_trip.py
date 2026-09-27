"""plan_trip 的決定性部分（不呼叫 Claude、不上網）：python -m pytest tests"""
import json
import sys
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plan_trip as pt  # noqa: E402

LAST = {"checkedAt": "2026-09-27T21:40", "summary": "最低 …", "options": [
    {"totalEst": 23000, "type": "LCC", "airline": "捷星 ＋ 虎航", "out": "11/21 02:30→06:05",
     "ret": "11/22 12:00→14:00", "note": "紅眼；兩張單程"},
    {"totalEst": 26000, "type": "MIX", "airline": "長榮 ＋ 虎航", "out": "11/21 06:35→10:25",
     "ret": "11/22 16:55→19:10", "note": "兩張單程"},
]}


def write_trip(d, t):
    d.mkdir(parents=True, exist_ok=True)
    (d / "trip.json").write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")


def test_flight_price_prefers_cheapest_non_red_eye(tmp_path):
    write_trip(tmp_path, {"flights": {"estTwdTotal": 9000, "advice": "AI 說很便宜"}})
    assert pt.apply_flight_price(tmp_path, LAST) is True
    f = json.loads((tmp_path / "trip.json").read_text(encoding="utf-8"))["flights"]
    assert f["estTwdTotal"] == 26000
    assert f["outbound"] == {"airline": "長榮", "no": "", "dep": "06:35", "arr": "10:25"}
    assert f["return"]["airline"] == "虎航" and f["return"]["dep"] == "16:55"
    assert f["advice"].startswith("Google Flights 2026-09-27 21:40 實查")


def test_flight_price_missing_marks_estimate(tmp_path):
    write_trip(tmp_path, {"flights": {"estTwdTotal": 9000, "advice": "x"}})
    assert pt.apply_flight_price(tmp_path, None) is False
    f = json.loads((tmp_path / "trip.json").read_text(encoding="utf-8"))["flights"]
    assert f["estTwdTotal"] == 9000 and f["advice"].startswith("票價未能實查")


def test_auto_pick_prefers_walkable_private_rooms(tmp_path):
    (tmp_path / "research").mkdir()
    hotels = [
        {"yad": "1", "name": "山奧溫泉", "total": 10000, "walkMin": None, "shared": False, "url": "u1"},
        {"yad": "2", "name": "膠囊", "total": 8000, "walkMin": 3, "shared": True, "url": "u2"},
        {"yad": "3", "name": "站前A", "total": 30000, "walkMin": 5, "shared": False, "url": "u3"},
        {"yad": "4", "name": "站前B", "total": 20000, "walkMin": 8, "shared": False, "url": "u4"},
    ]
    (tmp_path / "research" / "jalan-2026-11-21.json").write_text(json.dumps(
        {"checkedAt": "2026-09-27T21:40", "nights": 1, "hotels": hotels}), encoding="utf-8")
    t = {"stays": [{"city": "金澤", "station": "金沢駅", "checkin": "2026-11-21", "checkout": "2026-11-22",
                    "candidates": [{"name": "初稿飯店"}]}]}
    got = pt.auto_pick_stays(Namespace(travelers=2), tmp_path, t)["stays"][0]["candidates"]
    assert [c["name"] for c in got] == ["站前B", "站前A"]
    assert got[0]["estLocalPerNight"] == 20000 and got[0]["yad"] == "4"


def test_hotel_tracking_file_and_track_ids(tmp_path):
    write_trip(tmp_path, {"localToTwd": 0.21, "stays": [
        {"city": "金澤", "checkin": "2026-11-21", "checkout": "2026-11-22",
         "candidates": [{"name": "站前B", "yad": "4"}, {"name": "沒有編號"}]}]})
    pt.write_hotel_tracking(Namespace(travelers=2), tmp_path, tmp_path / "trip.json")
    t = json.loads((tmp_path / "trip.json").read_text(encoding="utf-8"))
    h = json.loads((tmp_path / "hotels.json").read_text(encoding="utf-8"))
    assert t["stays"][0]["candidates"][0]["trackId"] == "jalan-4-2026-11-21"
    assert "yad" not in t["stays"][0]["candidates"][0]
    assert [x["jalan"] for x in h["hotels"]] == ["4"] and h["rooms"] == 1
