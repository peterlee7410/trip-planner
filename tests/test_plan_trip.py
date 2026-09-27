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


def test_flight_price_overwrites_ai_price(tmp_path):
    write_trip(tmp_path, {"flights": {"arriveAirport": "KIX", "estTwdTotal": 9000, "advice": "AI 說很便宜"}})
    pick = {"airport": "KMQ", "option": LAST["options"][1], "hours": 16.0, "checkedAt": LAST["checkedAt"]}
    assert pt.apply_flight_price(tmp_path, pick, ["KIX 最佳為 …"]) is True
    f = json.loads((tmp_path / "trip.json").read_text(encoding="utf-8"))["flights"]
    assert f["estTwdTotal"] == 26000 and f["arriveAirport"] == f["departAirport"] == "KMQ"
    assert f["outbound"] == {"airline": "長榮", "no": "", "dep": "06:35", "arr": "10:25"}
    assert f["return"]["airline"] == "虎航" and f["return"]["dep"] == "16:55"
    assert f["advice"].startswith("Google Flights 2026-09-27 21:40 實查，KMQ 進出") and "比較：KIX" in f["advice"]


def test_decide_flights_copies_winner_config(tmp_path):
    for code, last in (("KIX", {**LAST, "options": LAST["options"][:1]}), ("KMQ", LAST)):
        w = tmp_path / "research" / f"fl-{code}"
        (w / "data").mkdir(parents=True)
        (w / "config.json").write_text(json.dumps({"destination": code}), encoding="utf-8")
        (w / "data" / "history.json").write_text(json.dumps([last]), encoding="utf-8")
    by_airport = {"KIX": {**LAST, "options": LAST["options"][:1]}, "KMQ": LAST}
    draft = {"localToTwd": 0.21, "flights": {"arriveAirport": "KIX", "airportOptions": [
        {"code": "KIX", "groundMinutes": 180, "groundLocalPerPerson": 9800},
        {"code": "KMQ", "groundMinutes": 40, "groundLocalPerPerson": 1400}]}}
    a = Namespace(start="2026-11-21", end="2026-11-22", travelers=2, hour_value=800, allow_redeye=False)
    pick, compare = pt.decide_flights(a, tmp_path, by_airport, draft)
    assert pick["airport"] == "KMQ" and len(compare) == 1 and compare[0].startswith("KIX")
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["destination"] == "KMQ"
    assert (tmp_path / "data" / "history.json").exists()


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


# ---------------------------------------------------------------- 航班挑選

def test_usable_hours_counts_only_daytime_after_transfers():
    # 17:10 到 KIX，+1h 通關 +180 分到金澤 = 21:10 → 當天只剩 0.8h；隔天 23:10 起飛 → 需 18:10 離開 → 08:00–18:10 = 10.2h
    h = pt.usable_hours("2026-11-21", "17:10", "2026-11-22", "23:10", 180)
    assert round(h, 1) == 11.0


def test_usable_hours_early_flight_beats_late_one():
    early = pt.usable_hours("2026-11-21", "10:25", "2026-11-22", "16:45", 40)
    late = pt.usable_hours("2026-11-21", "17:10", "2026-11-22", "11:45", 40)
    assert early > late


def opt(total, airline, out, ret, note=""):
    return {"totalEst": total, "type": "", "airline": airline, "out": out, "ret": ret, "note": note}


def test_choose_flight_trades_price_for_time():
    by_airport = {
        "KIX": {"checkedAt": "2026-09-27T21:56", "summary": "", "options": [
            opt(24227, "樂桃 ＋ 捷星", "11/21 13:40→17:10", "11/22 23:10→01:30"),
            opt(26800, "虎航 ＋ 捷星", "11/21 06:40→10:05", "11/22 23:10→01:30"),
        ]},
        "KMQ": {"checkedAt": "2026-09-27T21:56", "summary": "", "options": [
            opt(52156, "長榮 ＋ 虎航", "11/21 06:35→10:25", "11/22 16:45→19:30"),
        ]},
    }
    info = {"KIX": {"groundMinutes": 180, "groundLocalPerPerson": 9800},
            "KMQ": {"groundMinutes": 40, "groundLocalPerPerson": 1400}}
    best = pt.choose_flight(by_airport, info, "2026-11-21", "2026-11-22", travelers=2, fx=0.21, hour_value=800)
    assert best["airport"] == "KIX" and best["option"]["airline"] == "虎航 ＋ 捷星"   # 早到那班，不是最便宜那班
    assert best["hours"] > 18
    # 關西只剩晚到的班次時（11h 對小松 16h），時間夠值錢就改選小松
    by_airport["KIX"]["options"] = by_airport["KIX"]["options"][:1]
    cheap = pt.choose_flight(by_airport, info, "2026-11-21", "2026-11-22", travelers=2, fx=0.21, hour_value=800)
    dear = pt.choose_flight(by_airport, info, "2026-11-21", "2026-11-22", travelers=2, fx=0.21, hour_value=5000)
    assert cheap["airport"] == "KIX" and dear["airport"] == "KMQ"


# ---------------------------------------------------------------- 住宿挑選

def test_hotel_pool_drops_shared_and_far():
    hotels = [
        {"yad": "1", "name": "山奧溫泉", "total": 10000, "walkMin": None, "shared": False, "rating": 4.8},
        {"yad": "2", "name": "膠囊", "total": 8000, "walkMin": 3, "shared": True, "rating": 4.0},
        {"yad": "3", "name": "片町", "total": 45000, "walkMin": 5, "shared": False, "rating": 4.0},
        {"yad": "4", "name": "站前", "total": 72200, "walkMin": 4, "shared": False, "rating": 4.4},
        {"yad": "5", "name": "郊外", "total": 30000, "walkMin": 35, "shared": False, "rating": 4.1},
    ]
    assert [h["name"] for h in pt.hotel_pool(hotels)] == ["片町", "站前"]


def test_auto_pick_includes_cheapest_and_caps_price():
    pool = [{"yad": str(i), "name": n, "total": t, "walkMin": 5, "shared": False, "rating": r, "url": "u"}
            for i, (n, t, r) in enumerate([("便宜", 45000, 4.0), ("較好", 60000, 4.6), ("太貴", 113850, 4.8),
                                           ("普通", 50000, 3.9)])]
    names = [h["name"] for h in pt.pick_hotels(pool)]
    assert names[0] == "便宜" and "太貴" not in names and len(names) == 3


def test_red_eye_avoided_unless_allowed():
    by_airport = {"KIX": {"checkedAt": "t", "summary": "", "options": [
        opt(25903, "捷星日本航空", "11/21 02:30→06:00", "11/22 23:10→01:30", "紅眼"),
        opt(29000, "虎航", "11/21 06:40→10:05", "11/22 23:10→01:30"),
    ]}}
    info = {"KIX": {"groundMinutes": 180, "groundLocalPerPerson": 8500}}
    args = ("2026-11-21", "2026-11-22")
    assert pt.choose_flight(by_airport, info, *args, 2, 0.21, 800)["option"]["airline"] == "虎航"
    assert pt.choose_flight(by_airport, info, *args, 2, 0.21, 800, allow_redeye=True)["option"]["airline"] == "捷星日本航空"
    only_red = {"KIX": {**by_airport["KIX"], "options": by_airport["KIX"]["options"][:1]}}
    assert pt.choose_flight(only_red, info, *args, 2, 0.21, 800)["option"]["airline"] == "捷星日本航空"


def test_hotel_pool_uses_station_walk_or_short_bus():
    H = lambda n, t, **k: {"yad": n, "name": n, "total": t, "shared": False, "rating": 4, **k}
    hotels = [
        H("交流道旁", 38160, walkMin=3, stationWalk=None, busMin=None),        # 徒歩 3 分不是從車站
        H("片町", 45000, walkMin=5, stationWalk=None, busMin=13),              # 巴士 13 分＋下車步行
        H("站前", 72200, walkMin=4, stationWalk=4, busMin=None),
        H("白峰溫泉", 18700, walkMin=None, stationWalk=None, busMin=100),      # 巴士 100 分
    ]
    assert [h["name"] for h in pt.hotel_pool(hotels)] == ["片町", "站前"]


def test_infeasible_late_arrival_is_excluded():
    # 21:45 到 KIX，再坐 3 小時車 → 凌晨才到，沒有電車；即使最便宜也不能選
    by_airport = {"KIX": {"checkedAt": "t", "summary": "", "options": [
        opt(21643, "捷星日本航空", "11/21 18:15→21:45", "11/22 23:10→01:30"),
        opt(24227, "樂桃 ＋ 捷星", "11/21 13:40→17:10", "11/22 23:10→01:30"),
    ]}}
    info = {"KIX": {"groundMinutes": 180, "groundLocalPerPerson": 9000}}
    best = pt.choose_flight(by_airport, info, "2026-11-21", "2026-11-22", 2, 0.21, 800)
    assert best["option"]["airline"] == "樂桃 ＋ 捷星" and not best.get("warning")


def test_all_infeasible_keeps_best_with_warning():
    by_airport = {"KIX": {"checkedAt": "t", "summary": "", "options": [
        opt(21643, "捷星日本航空", "11/21 18:15→21:45", "11/22 23:10→01:30")]}}
    info = {"KIX": {"groundMinutes": 180, "groundLocalPerPerson": 9000}}
    best = pt.choose_flight(by_airport, info, "2026-11-21", "2026-11-22", 2, 0.21, 800)
    assert best["option"]["airline"] == "捷星日本航空" and "23:00" in best["warning"]


def test_feasible_checks_both_ends():
    assert pt.feasible("2026-11-21", "17:10", "2026-11-22", "23:10", 180)
    assert not pt.feasible("2026-11-21", "21:45", "2026-11-22", "23:10", 180)   # 到不了
    assert not pt.feasible("2026-11-21", "10:00", "2026-11-22", "07:15", 180)   # 要凌晨出發去機場
