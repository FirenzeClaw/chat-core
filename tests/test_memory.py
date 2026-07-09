"""MemoryStore unit tests."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from chat_core.core.types import MemoryEntry, MemoryLink, RelationType
from chat_core.systems.memory import MemoryStore


# ── Test helpers ──────────────────────────────────────────────

def make_entry(
    namespace: str = "user/facts",
    key: str = "test_key",
    value: dict | None = None,
    layer: str = "gist",
    salience: float = 5.0,
    entity_type: str = "",
    topic_tags: list[str] | None = None,
    emotional_tags: dict | None = None,
    ttl: int | None = None,
    expires_at: datetime | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        namespace=namespace,
        key=key,
        value=value or {"content": "test value"},
        layer=layer,
        salience=salience,
        entity_type=entity_type,
        topic_tags=topic_tags or [],
        emotional_tags=emotional_tags,
        ttl=ttl,
        expires_at=expires_at,
    )


class TestMemoryStore:
    """MemoryStore CRUD and search tests."""

    @pytest.mark.asyncio
    async def test_open_close(self):
        store = MemoryStore(":memory:")
        await store.open()
        assert store._db is not None
        await store.close()
        assert store._db is None

    @pytest.mark.asyncio
    async def test_save_and_get(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            entry = make_entry(namespace="user/facts", key="name", value={"name": "Alice"})
            await store.save(entry)

            retrieved = await store.get("user/facts", "name")
            assert retrieved is not None
            assert retrieved.namespace == "user/facts"
            assert retrieved.key == "name"
            assert retrieved.value == {"name": "Alice"}
            assert retrieved.layer == "gist"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_save_overwrite(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            e1 = make_entry(namespace="user/facts", key="key1", value={"v": 1})
            await store.save(e1)

            e2 = make_entry(namespace="user/facts", key="key1", value={"v": 2})
            await store.save(e2)

            retrieved = await store.get("user/facts", "key1")
            assert retrieved is not None
            assert retrieved.value == {"v": 2}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            entry = make_entry(namespace="tmp", key="del_me", value={"x": 1})
            await store.save(entry)

            assert await store.get("tmp", "del_me") is not None

            await store.delete("tmp", "del_me")
            assert await store.get("tmp", "del_me") is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_fts5(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Save entries with Chinese text
            e1 = make_entry(
                namespace="user/facts", key="fact1",
                value={"content": "用户喜欢蓝色"},
                topic_tags=["颜色"],
            )
            e2 = make_entry(
                namespace="user/facts", key="fact2",
                value={"content": "用户讨厌红色"},
                topic_tags=["颜色"],
            )
            e3 = make_entry(
                namespace="user/facts", key="fact3",
                value={"content": "用户住在北京"},
                topic_tags=["地点"],
            )
            await store.save(e1)
            await store.save(e2)
            await store.save(e3)

            # FTS5 search for "喜欢" (present in e1)
            results = await store.search("喜欢")
            assert len(results) >= 1
            keys = [r.key for r in results]
            assert "fact1" in keys
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_like_fallback(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Use a mid-token substring that FTS5 won't match
            # but LIKE will find in the JSON value
            e1 = make_entry(
                namespace="user/facts", key="unique_key_xyz",
                value={"content": "searchlikefallbacktestvalue"},
            )
            await store.save(e1)

            # "likefallback" is a substring inside the value.
            # FTS5 tokenizer won't index it as a separate token,
            # so FTS5 returns empty. LIKE fallback should catch it.
            results = await store.search("likefallback")
            assert len(results) >= 1
            assert results[0].key == "unique_key_xyz"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_link_and_get_links(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            e1 = make_entry(namespace="user/facts", key="alice")
            e2 = make_entry(namespace="user/facts", key="bob")
            await store.save(e1)
            await store.save(e2)

            await store.link("user/facts", "alice", "user/facts", "bob", RelationType.RELATED_TO)

            links = await store.get_links("user/facts", "alice")
            assert len(links) >= 1
            assert any(
                l.from_key == "user/facts/alice" and l.to_key == "user/facts/bob"
                for l in links
            )
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_tag(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            entry = make_entry(namespace="user/facts", key="mem1", value={"text": "hello"})
            await store.save(entry)

            # Tag with emotion data
            await store.tag("user/facts", "mem1", {"joy": 0.8, "surprise": 0.3})

            retrieved = await store.get("user/facts", "mem1")
            assert retrieved is not None
            assert retrieved.emotional_tags is not None
            assert retrieved.emotional_tags.get("joy") == 0.8
            assert retrieved.emotional_tags.get("surprise") == 0.3
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_query_namespace_prefix(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            e1 = make_entry(namespace="user/facts", key="k1", value={"v": 1})
            e2 = make_entry(namespace="user/prefs", key="k2", value={"v": 2})
            e3 = make_entry(namespace="user/facts", key="k3", value={"v": 3})
            await store.save(e1)
            await store.save(e2)
            await store.save(e3)

            results = await store.query("user/facts")
            assert len(results) == 2
            keys = {r.key for r in results}
            assert keys == {"k1", "k3"}
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Save with a very short TTL (1 second) and old created_at
            past = datetime.now() - timedelta(seconds=10)
            entry = MemoryEntry(
                namespace="cache", key="temp",
                value={"data": "ephemeral"},
                ttl=1,  # 1 second TTL
                created_at=past,
            )
            await store.save(entry)

            # Query should filter out the expired entry
            results = await store.query("cache")
            assert len(results) == 0  # expired due to TTL
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_namespace_isolation(self):
        store = MemoryStore(":memory:")
        await store.open()
        try:
            e1 = make_entry(namespace="user/facts", key="k1", value={"v": "user"})
            e2 = make_entry(namespace="self/thoughts", key="k2", value={"v": "self"})
            await store.save(e1)
            await store.save(e2)

            # Query only self/* namespace
            results = await store.query("self/")
            assert len(results) == 1
            assert results[0].key == "k2"
            assert results[0].namespace == "self/thoughts"
        finally:
            await store.close()
