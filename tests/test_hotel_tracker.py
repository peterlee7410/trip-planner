"""hotel_tracker 的離線測試：python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import hotel_tracker as ht  # noqa: E402

TIMEOUT = {"error": "Page.wait_for_selector: Timeout 30000ms exceeded."}


def test_booking_only_all_failed_is_not_success():
    cur = {"a": {"booking": TIMEOUT}, "b": {"booking": TIMEOUT}}
    assert ht.any_success(cur) is False


def test_booking_only_one_ok_is_success():
    cur = {"a": {"booking": TIMEOUT}, "b": {"booking": {"found": True, "soldOut": False, "price": 3200}}}
    assert ht.any_success(cur) is True


def test_jalan_ok_booking_failed_is_success():
    cur = {"a": {"jalan": {"available": True, "min": 14268}, "booking": TIMEOUT}}
    assert ht.any_success(cur) is True


def test_all_sources_failed_is_not_success():
    cur = {"a": {"jalan": TIMEOUT, "booking": TIMEOUT}, "b": {"jalan": TIMEOUT}}
    assert ht.any_success(cur) is False


def test_no_sources_is_not_success():
    assert ht.any_success({"a": {}}) is False
    assert ht.any_success({}) is False


def test_brief_omits_sources_not_configured():
    assert ht.brief({"booking": {"found": True, "soldOut": False, "price": 3200}}) == {"booking": 3200}
    assert ht.brief({"jalan": {"available": False, "calendar": "×"}}) == {"jalan": "客滿(×)"}


CFG = {"jpy_to_twd": 0.21, "alert_drop_jpy": 1000, "hotels": [{"id": "a", "nameZh": "甲飯店"}]}
BOOKED = {"found": True, "soldOut": False, "price": 3200}


def test_timeout_is_not_reported_as_sold_out():
    prev = {"a": {"booking": BOOKED}}
    cur = {"a": {"booking": TIMEOUT}}
    assert ht.alerts(CFG, cur, prev) == []


def test_recovery_after_timeout_is_not_reported_as_new_vacancy():
    prev = {"a": {"booking": TIMEOUT}}
    cur = {"a": {"booking": BOOKED}}
    assert ht.alerts(CFG, cur, prev) == []


def test_real_sold_out_is_still_reported():
    prev = {"a": {"jalan": {"available": True, "min": 14268}}}
    cur = {"a": {"jalan": {"available": False, "calendar": "×"}}}
    assert [m[:2] for m in ht.alerts(CFG, cur, prev)] == ["🔴 "]


def test_real_new_vacancy_is_still_reported():
    prev = {"a": {"jalan": {"available": False, "calendar": "×"}}}
    cur = {"a": {"jalan": {"available": True, "min": 14268}}}
    assert [m[:2] for m in ht.alerts(CFG, cur, prev)] == ["🟢 "]
