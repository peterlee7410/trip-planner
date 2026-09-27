"""agent_server.py 的離線測試：python -m pytest tests"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_server as srv  # noqa: E402

REQ = {"destination": "金澤", "origin": "TPE", "start": "2026-11-21", "end": "2026-11-22", "travelers": 2,
       "arriveBy": "16:00", "departAfter": "10:00", "budget": 20000, "hotelBudget": 4000, "pace": "一般",
       "airline": "LCC 與傳統皆可", "interests": ["美食", "寺社與歷史"], "luggage": "去程 1 件托運、回程 2 件托運",
       "notes": "不吃生魚片"}


def test_build_args_maps_form_to_plan_trip():
    args = srv.build_args(REQ, "trip-20261121-ab12")
    assert args[:2] == ["plan_trip.py", "--slug"] and "trip-20261121-ab12" in args
    kv = dict(zip(args[1::2], args[2::2]))
    assert kv["--dest"] == "金澤" and kv["--start"] == "2026-11-21" and kv["--travelers"] == "2"
    assert kv["--arrive-by"] == "16:00" and kv["--depart-after"] == "10:00" and kv["--hotel-budget"] == "4000"
    assert "美食" in kv["--notes"] and "不吃生魚片" in kv["--notes"] and "--allow-redeye" not in args


def test_red_eye_preference_enables_flag():
    assert "--allow-redeye" in srv.build_args({**REQ, "airline": "越便宜越好（可紅眼）"}, "trip-x")


@pytest.mark.parametrize("bad", [
    {"destination": ""}, {"start": "2026/11/21"}, {"end": "2026-11-20"}, {"travelers": 0},
    {"end": "2026-12-30"}, {"arriveBy": "25:00"}, {"destination": "x" * 81},
])
def test_validate_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        srv.validate({**REQ, **bad})


def test_validate_accepts_form():
    assert srv.validate(dict(REQ))["travelers"] == 2


def test_origin_must_be_this_server():
    assert srv.origin_ok("http://localhost:8787", 8787)
    assert srv.origin_ok("http://127.0.0.1:8787", 8787)
    assert not srv.origin_ok("https://evil.example", 8787)
    assert not srv.origin_ok(None, 8787)          # 沒有 Origin 的跨站表單也不收
    assert not srv.origin_ok("http://localhost:9999", 8787)
