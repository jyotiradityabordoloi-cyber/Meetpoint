import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_maps
from app.main import create_app

from .fakes import FakeMapClient


@pytest.fixture
def make_client(tmp_path):
    def _make(maps=None, **overrides):
        settings = Settings(
            google_maps_api_key="test" if maps is not None else "",
            database_url=f"sqlite:///{tmp_path / 'test.db'}",
            rate_limit_per_minute=overrides.pop("rate_limit_per_minute", 100),
            **overrides,
        )
        app = create_app(settings)

        async def _maps():
            yield maps

        app.dependency_overrides[get_maps] = _maps
        return TestClient(app)

    return _make


def _group(c, **kw):
    r = c.post("/api/groups", json={"name": "Friday", "travel_mode": "DRIVE", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_health(make_client):
    c = make_client()
    assert c.get("/health").json() == {"ok": True, "maps_configured": False}


def test_group_crud_and_member_lifecycle(make_client):
    c = make_client(FakeMapClient())
    g = _group(c, venue_types=["cafe", "park"], meet_at="2030-01-01T19:00:00+05:30")
    assert len(g["id"]) == 12 and g["venue_types"] == ["cafe", "park"]

    r = c.patch(f"/api/groups/{g['id']}", json={"travel_mode": "TRANSIT", "meet_at": None})
    assert r.status_code == 200 and r.json()["travel_mode"] == "TRANSIT" and r.json()["meet_at"] is None

    m1 = c.post(f"/api/groups/{g['id']}/members", json={"name": "Inês", "lat": 38.72, "lng": -9.14}).json()
    m2 = c.post(f"/api/groups/{g['id']}/members", json={"name": "Ana", "address": "Rua Augusta"}).json()
    assert m2["address"].startswith("Rua Augusta") and m2["lat"]
    got = c.get(f"/api/groups/{g['id']}").json()
    assert [m["name"] for m in got["members"]] == ["Inês", "Ana"]

    assert c.delete(f"/api/groups/{g['id']}/members/{m1['id']}").status_code == 204
    r = c.delete(f"/api/groups/{g['id']}/members/{m1['id']}")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "MEMBER_NOT_FOUND"


def test_unknown_group_error_shape(make_client):
    r = make_client().get("/api/groups/doesnotexist")
    assert r.status_code == 404
    assert r.json() == {"detail": {"code": "GROUP_NOT_FOUND", "message": "This group doesn't exist or was deleted."}}


@pytest.mark.parametrize(
    "body",
    [
        {"name": "X", "lat": 91, "lng": 0},
        {"name": "X", "lat": 0},
        {"name": "X"},
        {"name": "", "lat": 0, "lng": 0},
        {"name": "X", "lat": 0, "lng": 0, "travel_mode": "HELICOPTER"},
    ],
)
def test_member_validation(make_client, body):
    c = make_client()
    g = _group(c)
    r = c.post(f"/api/groups/{g['id']}/members", json=body)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_group_validation(make_client):
    c = make_client()
    assert c.post("/api/groups", json={"venue_types": ["casino"]}).status_code == 422
    assert c.post("/api/groups", json={"meet_at": "2030-01-01T19:00:00"}).status_code == 422  # no offset


def test_too_many_members(make_client):
    c = make_client(max_members=3)
    g = _group(c)
    for i in range(3):
        assert c.post(f"/api/groups/{g['id']}/members", json={"name": f"P{i}", "lat": i, "lng": i}).status_code == 201
    r = c.post(f"/api/groups/{g['id']}/members", json={"name": "P4", "lat": 1, "lng": 1})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "TOO_MANY_MEMBERS"


def test_address_errors(make_client):
    c = make_client(FakeMapClient())
    g = _group(c)
    r = c.post(f"/api/groups/{g['id']}/members", json={"name": "X", "address": "Nowhere street"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "ADDRESS_NOT_FOUND"
    assert "Nowhere street" in r.json()["detail"]["message"]

    c2 = make_client(FakeMapClient(fail_geocode=True))
    g2 = _group(c2)
    r = c2.post(f"/api/groups/{g2['id']}/members", json={"name": "X", "address": "Main St"})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "GEOCODER_FAILED"

    c3 = make_client(None)
    g3 = _group(c3)
    r = c3.post(f"/api/groups/{g3['id']}/members", json={"name": "X", "address": "Main St"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "GEOCODER_UNAVAILABLE"


def test_geocode_proxy(make_client):
    c = make_client(FakeMapClient())
    r = c.get("/api/geocode", params={"q": "Rossio"})
    assert r.status_code == 200 and r.json()[0]["label"].startswith("Rossio")
    assert c.get("/api/geocode", params={"q": "nowhere"}).json() == []
    assert make_client(None).get("/api/geocode", params={"q": "abc"}).status_code == 503


def test_meeting_point_end_to_end_and_history(make_client):
    c = make_client(FakeMapClient())
    g = _group(c)
    r = c.post(f"/api/groups/{g['id']}/meeting-point")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "NOT_ENOUGH_MEMBERS"

    for name, lat, lng in [("A", 38.75, -9.17), ("B", 38.70, -9.10), ("C", 38.73, -9.20)]:
        c.post(f"/api/groups/{g['id']}/members", json={"name": name, "lat": lat, "lng": lng})
    r = c.post(f"/api/groups/{g['id']}/meeting-point")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["id"] and rec["group_id"] == g["id"] and rec["created_at"].endswith("Z")
    assert 1 <= len(rec["venues"]) <= 5
    assert {t["name"] for t in rec["venues"][0]["travel"]} == {"A", "B", "C"}

    hist = c.get(f"/api/groups/{g['id']}/recommendations").json()
    assert len(hist) == 1 and hist[0]["id"] == rec["id"]

    # Deleting the group's last members cascades nothing odd; group still readable.
    assert c.get(f"/api/groups/{g['id']}").status_code == 200


def test_meeting_point_without_key_returns_estimates(make_client):
    c = make_client(None)
    g = _group(c)
    for lat in (10.0, 10.05):
        c.post(f"/api/groups/{g['id']}/members", json={"name": "P", "lat": lat, "lng": 20.0})
    rec = c.post(f"/api/groups/{g['id']}/meeting-point").json()
    assert rec["estimated"] is True and rec["venues"] == [] and rec["zones"]


def test_rate_limit(make_client):
    c = make_client(FakeMapClient(), rate_limit_per_minute=2)
    g = _group(c)
    for lat in (38.7, 38.75):
        c.post(f"/api/groups/{g['id']}/members", json={"name": "P", "lat": lat, "lng": -9.1})
    assert c.post(f"/api/groups/{g['id']}/meeting-point").status_code == 200
    assert c.post(f"/api/groups/{g['id']}/meeting-point").status_code == 200
    r = c.post(f"/api/groups/{g['id']}/meeting-point")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED"


def test_purge_old_groups(make_client):
    from datetime import datetime, timedelta, timezone

    from app.db import session_factory
    from app.models import Group, Member
    from app.purge import purge_old_groups

    c = make_client()
    old = _group(c)
    new = _group(c)
    c.post(f"/api/groups/{old['id']}/members", json={"name": "P", "lat": 1, "lng": 1})
    with session_factory()() as db:
        g = db.get(Group, old["id"])
        g.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.commit()
    assert purge_old_groups(30) == 1
    assert c.get(f"/api/groups/{old['id']}").status_code == 404
    assert c.get(f"/api/groups/{new['id']}").status_code == 200
    with session_factory()() as db:
        assert db.query(Member).filter(Member.group_id == old["id"]).count() == 0
