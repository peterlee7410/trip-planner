"""tools/wiki_photos.py 的離線測試（假的 API 回應，不上網）：python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import wiki_photos as wp  # noqa: E402

PAGES = {  # 標題 → (頁面圖片檔名, 座標)
    "兼六園": ("Kenrokuen10-r.jpg", (36.562, 136.662)),
    "ひがし茶屋街": ("Higashi.jpg", (36.572, 136.667)),
    "HARUKA": (None, None),                      # 歌手、列車名：沒有座標，不能用
    "金沢": ("Kanazawa-city.jpg", None),            # 有圖但沒座標（城市總覽頁）
    "遠方の寺": ("Far.jpg", (43.06, 141.35)),        # 札幌附近，離其他站太遠
    "非自由": ("Fairuse.jpg", (36.57, 136.66)),      # 圖不在 Commons（非自由授權）
}
FILES = {
    "Kenrokuen10-r.jpg": {"license": "CC BY 2.5", "artist": "<a href='x'>Oilstreet</a>"},
    "Higashi.jpg": {"license": "CC BY-SA 4.0", "artist": "Someone"},
    "Far.jpg": {"license": "Public domain", "artist": "PD"},
    "Kanazawa-city.jpg": {"license": "CC BY-SA 3.0", "artist": "X"},
}


def fake_fetch(url, params):
    if "wikipedia.org" in url:
        title = params["titles"]
        img, coord = PAGES.get(title, (None, None))
        page = {"title": title}
        if img:
            page["pageimage"] = img
        if coord:
            page["coordinates"] = [{"lat": coord[0], "lon": coord[1]}]
        return {"query": {"pages": {"1": page}}}
    name = params["titles"].removeprefix("File:")
    f = FILES.get(name)
    if not f:
        return {"query": {"pages": {"-1": {"missing": ""}}}}
    return {"query": {"pages": {"1": {"imageinfo": [{
        "thumburl": f"https://upload.wikimedia.org/thumb/{name}/640px-{name}",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}",
        "extmetadata": {"LicenseShortName": {"value": f["license"]}, "Artist": {"value": f["artist"]}}}]}}}}


def test_lookup_returns_free_photo_with_credit():
    p = wp.lookup("兼六園", fetch=fake_fetch, cache={})
    assert p["src"].endswith("640px-Kenrokuen10-r.jpg")
    assert p["license"] == "CC BY 2.5" and p["artist"] == "Oilstreet"
    assert p["page"] == "https://commons.wikimedia.org/wiki/File:Kenrokuen10-r.jpg"


def test_lookup_skips_pages_without_coordinates_or_free_file():
    for t in ("HARUKA", "金沢", "非自由", "不存在"):
        assert wp.lookup(t, fetch=fake_fetch, cache={}) is None, t


def test_annotate_trip_drops_far_outliers_and_dedupes():
    trip = {"days": [
        {"date": "2026-11-21", "map": [{"label": "兼六園"}, {"label": "HARUKA"}, {"label": "兼六園"}]},
        {"date": "2026-11-22", "mapStops": ["ひがし茶屋街（東茶屋街）", "遠方の寺"]},
    ]}
    wp.annotate(trip, fetch=fake_fetch, cache={})
    assert [p["name"] for p in trip["days"][0]["photos"]] == ["兼六園"]
    assert [p["name"] for p in trip["days"][1]["photos"]] == ["ひがし茶屋街"]   # 札幌那張離太遠被丟掉


def test_annotate_with_no_photos_at_all():
    trip = {"days": [{"date": "2026-11-21", "mapStops": ["HARUKA"]}]}
    assert wp.annotate(trip, fetch=fake_fetch, cache={}) == 0 and trip["days"][0]["photos"] == []


def test_same_photo_only_on_first_day():
    trip = {"days": [{"date": "d1", "mapStops": ["兼六園"]}, {"date": "d2", "mapStops": ["兼六園", "ひがし茶屋街"]}]}
    wp.annotate(trip, fetch=fake_fetch, cache={})
    assert [p["name"] for p in trip["days"][0]["photos"]] == ["兼六園"]
    assert [p["name"] for p in trip["days"][1]["photos"]] == ["ひがし茶屋街"]


def test_cjk_names_do_not_fall_back_to_english_city_pages():
    calls = []

    def fetch(url, params):
        calls.append(url)
        if "en.wikipedia" in url:   # 英文「Kyoto」城市頁：有座標也有圖（金閣寺），不能拿來代表「京都」這個轉車站
            return {"query": {"pages": {"1": {"title": "Kyoto", "pageimage": "Kinkakuji.jpg",
                                              "coordinates": [{"lat": 35.0, "lon": 135.7}]}}}}
        if "ja.wikipedia" in url:
            return {"query": {"pages": {"1": {"title": "京都"}}}}
        return fake_fetch(url, params)

    assert wp.lookup("京都", fetch=fetch, cache={}) is None
    assert not any("en.wikipedia" in u for u in calls)
