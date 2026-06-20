"""Event group persistence and manual edit behavior."""

from __future__ import annotations

from typing import Any

import pytest

from mediarefinery.service.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    db = StateStore(tmp_path / "state.db")
    db.initialize()
    db.upsert_user(user_id="alice", email="alice@example.invalid")
    db.upsert_user(user_id="bob", email="bob@example.invalid")
    try:
        yield db
    finally:
        db.close()


def _analysis(
    asset_id: str,
    event_key: str,
    *,
    day: str = "2026-01-01",
    city: str = "Berlin",
    country: str = "Germany",
    person: str = "Alice",
    album: str = "travel",
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "primary_category_id": "sfw",
        "media_info": {
            "kind": "image",
            "mime_type": "image/jpeg",
            "city": city,
            "country": country,
            "albums": [album],
        },
        "safety": {"label": "sfw", "confidence": 0.98, "review_needed": False},
        "people": [{"id": f"person-{person.lower()}", "name": person}],
        "semantic": {"terms": ["travel", city.lower()]},
        "events": {
            "event_key": event_key,
            "status": "auto",
            "day": day,
            "place": f"{city}-{country}",
        },
        "review_queues": ["sfw", "people"],
    }


def _seed_visible_asset(
    store: StateStore,
    user_id: str,
    asset_id: str,
    event_key: str,
    **analysis_kwargs: str,
) -> None:
    scoped = store.with_user(user_id)
    scoped.upsert_asset(asset_id=asset_id, media_type="image")
    run_id = scoped.start_run(dry_run=True, command="scan")
    scoped.record_action(
        run_id=run_id,
        asset_id=asset_id,
        action_name="manual_review",
        dry_run=True,
        would_apply=True,
        success=True,
    )
    scoped.record_asset_analysis(
        asset_id=asset_id,
        analysis=_analysis(asset_id, event_key, **analysis_kwargs),
    )
    scoped.finish_run(run_id, status="completed")


def test_record_asset_analysis_creates_auto_event_group(store: StateStore) -> None:
    """Test record asset analysis creates auto event group."""
    _seed_visible_asset(store, "alice", "asset-1", "2026-01-01::berlin::alice")

    scoped = store.with_user("alice")
    groups = scoped.list_event_groups()
    assert len(groups) == 1
    assert groups[0]["title"] == "2026-01-01 - Berlin, Germany - Alice - travel"
    assert groups[0]["status"] == "auto"
    assert groups[0]["asset_count"] == 1

    assets, _ = scoped.list_event_assets_paginated(
        event_id=str(groups[0]["event_id"]),
        cursor=None,
        page_size=10,
    )
    assert [row["asset_id"] for row in assets] == ["asset-1"]
    assert assets[0]["event_title"] == groups[0]["title"]


def test_manual_rename_survives_rescan_and_is_audited(store: StateStore) -> None:
    """Test manual rename survives rescan and is audited."""
    event_key = "2026-01-01::berlin::alice"
    _seed_visible_asset(store, "alice", "asset-1", event_key)
    scoped = store.with_user("alice")
    event_id = str(scoped.list_event_groups()[0]["event_id"])

    scoped.rename_event_group(event_id=event_id, title="Alice birthday")
    scoped.record_asset_analysis(
        asset_id="asset-1",
        analysis=_analysis(
            "asset-1",
            event_key,
            city="Paris",
            country="France",
            album="camera",
        ),
    )

    group = scoped.get_event_group(event_id)
    assert group is not None
    assert group["title"] == "Alice birthday"
    assert group["status"] == "manual"
    assert [row["action"] for row in scoped.list_audit()] == ["event.rename"]


def test_merge_combines_memberships_and_pins_future_matching_assets(
    store: StateStore,
) -> None:
    """Test merge combines memberships and pins future matching assets."""
    source_key = "2026-01-01::munich"
    _seed_visible_asset(store, "alice", "asset-1", "2026-01-01::berlin")
    _seed_visible_asset(
        store,
        "alice",
        "asset-2",
        source_key,
        city="Munich",
        person="Bob",
    )
    scoped = store.with_user("alice")
    groups = {str(group["auto_key"]): group for group in scoped.list_event_groups()}
    target_id = str(groups["2026-01-01::berlin"]["event_id"])
    source_id = str(groups[source_key]["event_id"])

    scoped.merge_event_groups(target_event_id=target_id, source_event_ids=[source_id])
    _seed_visible_asset(
        store,
        "alice",
        "asset-3",
        source_key,
        city="Munich",
        person="Bob",
    )

    target = scoped.get_event_group(target_id)
    assert target is not None
    assert target["asset_count"] == 3
    assert scoped.get_event_group(source_id) is None
    assets, _ = scoped.list_event_assets_paginated(
        event_id=target_id,
        cursor=None,
        page_size=10,
    )
    assert {row["asset_id"] for row in assets} == {"asset-1", "asset-2", "asset-3"}
    assert "event.merge" in [row["action"] for row in scoped.list_audit()]


def test_split_remove_and_reset_restore_automatic_grouping(
    store: StateStore,
) -> None:
    """Test split remove and reset restore automatic grouping."""
    event_key = "2026-01-01::berlin"
    _seed_visible_asset(store, "alice", "asset-1", event_key)
    _seed_visible_asset(store, "alice", "asset-2", event_key)
    scoped = store.with_user("alice")
    original_id = str(scoped.list_event_groups()[0]["event_id"])

    split = scoped.split_event_group(
        event_id=original_id,
        asset_ids=["asset-1"],
        title="Museum stop",
    )
    split_id = str(split["event_id"])
    original = scoped.get_event_group(original_id)
    assert original is not None
    assert original["asset_count"] == 1
    split_group = scoped.get_event_group(split_id)
    assert split_group is not None
    assert split_group["asset_count"] == 1

    scoped.reset_event_group(event_id=split_id)
    restored = scoped.get_event_group(original_id)
    assert restored is not None
    assert restored["asset_count"] == 2
    assert scoped.get_event_group(split_id) is None

    scoped.remove_asset_from_event(event_id=original_id, asset_id="asset-1")
    scoped.record_asset_analysis(
        asset_id="asset-1",
        analysis=_analysis("asset-1", event_key),
    )
    after_remove = scoped.get_event_group(original_id)
    assert after_remove is not None
    assert after_remove["asset_count"] == 1
    actions = [row["action"] for row in scoped.list_audit()]
    assert "event.split" in actions
    assert "event.reset" in actions
    assert "event.asset.remove" in actions


def test_event_groups_are_tenant_isolated(store: StateStore) -> None:
    """Test event groups are tenant isolated."""
    event_key = "2026-01-01::shared"
    _seed_visible_asset(store, "alice", "asset-a", event_key)
    _seed_visible_asset(store, "bob", "asset-b", event_key, person="Bob")
    alice = store.with_user("alice")
    bob = store.with_user("bob")
    alice_event = alice.list_event_groups()[0]
    bob_event = bob.list_event_groups()[0]

    alice.rename_event_group(event_id=str(alice_event["event_id"]), title="Alice event")

    renamed = alice.get_event_group(str(alice_event["event_id"]))
    untouched = bob.get_event_group(str(bob_event["event_id"]))
    assert renamed is not None
    assert untouched is not None
    assert renamed["title"] == "Alice event"
    assert untouched["title"] != "Alice event"
    assert alice.list_event_assets_paginated(
        event_id=str(alice_event["event_id"]),
        cursor=None,
        page_size=10,
    )[0][0]["asset_id"] == "asset-a"
    assert bob.list_event_assets_paginated(
        event_id=str(bob_event["event_id"]),
        cursor=None,
        page_size=10,
    )[0][0]["asset_id"] == "asset-b"
