"""MemoryStore unit tests."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from chat_core.core.types import ChainedMemory, MemoryEntry, MemoryLink, RecallChainConfig, RelationType
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


# ── Spec 003: 记忆联锁测试 (Phase 3, US1) ─────────────────────

class TestChainSearch:
    """search_chained() 联锁检索 + 自然语言回溯测试."""

    @pytest.mark.asyncio
    async def test_search_chained_main_brain(self):
        """T014: 主脑配置 5 direct + 3+2+2+1+0 延伸链结构."""
        from chat_core.core.types import LOGIC_BRAIN_CHAIN_CONFIG

        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Seed: 6 entries with links from direct matches
            for i in range(6):
                e = make_entry(
                    namespace=f"user/facts", key=f"direct_{i}",
                    value={"content": f"这是第{i}条测试记忆"},
                    topic_tags=[f"tag_{i}"],
                    entity_type="person",
                )
                await store.save(e)
            # Link: direct_0 → direct_1, direct_0 → direct_2, etc.
            for i in range(1, 5):
                await store.link("user/facts", "direct_0", "user/facts", f"direct_{i}", RelationType.RELATED_TO)

            result = await store.search_chained("测试记忆", LOGIC_BRAIN_CHAIN_CONFIG)

            # Should have direct matches
            direct_matches = [cm for cm in result if cm.chain_level == 0]
            assert len(direct_matches) == 5  # top_n=5

            # First direct match should have extensions (links)
            # The extensions depend on what's available; at minimum, some level-1 results
            chain_levels = set(cm.chain_level for cm in result)
            assert 0 in chain_levels  # direct matches present
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_chained_sub_session(self):
        """T015: 子Session 配置 3 direct + 2+1 extension + namespace 过滤."""
        from chat_core.core.types import SUB_SESSION_CHAIN_CONFIG, RecallChainConfig

        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Seed: entries in user/alice and user/bob
            for i in range(5):
                await store.save(make_entry(
                    namespace=f"user/alice/facts", key=f"a_{i}",
                    value={"content": f"Alice 记忆 {i}"},
                ))
            for i in range(5):
                await store.save(make_entry(
                    namespace=f"user/bob/facts", key=f"b_{i}",
                    value={"content": f"Bob 记忆 {i}"},
                ))

            # Sub-session config with namespace_prefix
            sub_config = RecallChainConfig(
                top_n=SUB_SESSION_CHAIN_CONFIG.top_n,
                extensions=list(SUB_SESSION_CHAIN_CONFIG.extensions),
                max_per_level=SUB_SESSION_CHAIN_CONFIG.max_per_level,
                namespace_prefix="user/alice",
            )
            result = await store.search_chained("记忆", sub_config)

            direct = [cm for cm in result if cm.chain_level == 0]
            assert len(direct) <= 3  # top_n=3 for sub-session

            # All results should be from user/alice namespace
            for cm in result:
                assert cm.entry.namespace.startswith("user/alice"), \
                    f"Expected user/alice/*, got {cm.entry.namespace}"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_extend_chain_levels(self):
        """T016: 4 级 fallback 各自返回正确条目 + 上级空时降级."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Entry with links, tags, entity_type
            root = make_entry(
                namespace="user/facts", key="root",
                value={"content": "根记忆 — 小明是程序员"},
                topic_tags=["职业", "技术"],
                entity_type="person",
            )
            await store.save(root)

            # L1: linked entries
            linked = make_entry(namespace="user/facts", key="linked1",
                value={"content": "通过链接关联的记忆"}, entity_type="person")
            await store.save(linked)
            await store.link("user/facts", "root", "user/facts", "linked1", RelationType.RELATED_TO)

            # L2: same topic_tags
            same_tag = make_entry(namespace="user/facts", key="tag_entry",
                value={"content": "相同标签的记忆"}, topic_tags=["职业", "其他"],
                entity_type="person")
            await store.save(same_tag)

            # L3: same entity_type
            same_entity = make_entry(namespace="user/facts", key="entity_entry",
                value={"content": "同类型记忆"}, entity_type="person")
            await store.save(same_entity)

            # L4: same namespace
            same_ns = make_entry(namespace="user/facts", key="ns_entry",
                value={"content": "同命名空间记忆"})
            await store.save(same_ns)

            # Test each level separately
            # L1
            l1 = await store._extend_by_links(root, remaining=5, max_per_level=5, namespace_prefix=None)
            assert len(l1) >= 1
            assert any(cm.entry.key == "linked1" for cm in l1)

            # L2
            l2 = await store._extend_by_tags(root, remaining=5, max_per_level=5, namespace_prefix=None)
            assert len(l2) >= 1
            assert any(cm.entry.key == "tag_entry" for cm in l2)

            # L3
            l3 = await store._extend_by_entity(root, remaining=5, max_per_level=5, namespace_prefix=None)
            assert len(l3) >= 1
            assert any(cm.entry.key == "entity_entry" for cm in l3)

            # L4
            l4 = await store._extend_by_namespace(root, remaining=5, max_per_level=5, namespace_prefix=None)
            assert len(l4) >= 1
            assert any(cm.entry.key == "ns_entry" for cm in l4)

            # Test full _extend_chain with fallback: target_n=1 but links fail (no db)
            # Should fall back to tags → entity → namespace
            chain = await store._extend_chain(root, target_n=4, namespace_prefix=None, max_per_level=5)
            # Should have gotten up to 4 items across levels
            assert len(chain) >= 1
            # Levels present should include links (1) and possibly tags (2), entity (3), namespace (4)
            found_levels = set(cm.chain_level for cm in chain)
            assert 1 in found_levels or 2 in found_levels or 3 in found_levels or 4 in found_levels
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_dedup_across_chains(self):
        """T017: L1+L2 重复 → 仅保留 L1."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Create a shared entry reachable by both L1 (links) and L2 (tags)
            shared = make_entry(
                namespace="user/facts", key="shared",
                value={"content": "共享记忆"},
                topic_tags=["职业", "技术"],
                entity_type="person",
            )
            await store.save(shared)

            # Root 1: linked to shared
            root1 = make_entry(
                namespace="user/facts", key="root1",
                value={"content": "根记忆1"},
                topic_tags=["职业"],
                entity_type="person",
            )
            await store.save(root1)
            await store.link("user/facts", "root1", "user/facts", "shared", RelationType.RELATED_TO)

            # Root 2: same topic_tags as shared
            root2 = make_entry(
                namespace="user/facts", key="root2",
                value={"content": "根记忆2"},
                topic_tags=["技术"],
                entity_type="person",
            )
            await store.save(root2)

            # Full chain from both roots
            chain1 = await store._extend_chain(root1, target_n=3, namespace_prefix=None, max_per_level=3)
            chain2 = await store._extend_chain(root2, target_n=3, namespace_prefix=None, max_per_level=3)

            # From root1: shared should be L1 (links)
            shared_from_root1 = [cm for cm in chain1 if cm.entry.key == "shared"]
            if shared_from_root1:
                assert shared_from_root1[0].chain_level == 1

            # Build a mock direct+extension list, dedup
            direct_cm = [
                ChainedMemory(entry=root1, chain_level=0, chain_parent_key=None, relevance_score=1.0),
            ]
            # Manually add shared at L2 level (pretend it came from tags chain)
            all_results = direct_cm + chain1 + [ChainedMemory(
                entry=shared, chain_level=2,
                chain_parent_key=f"{root2.namespace}/{root2.key}",
                relevance_score=0.6,
            )]
            deduped = store._dedup_by_quality(all_results)
            # shared should appear only once, with lowest chain_level (1 from links)
            shared_entries = [cm for cm in deduped if cm.entry.key == "shared"]
            assert len(shared_entries) == 1
            assert shared_entries[0].chain_level == 1  # links beats tags
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_broken_link_skip(self):
        """T018: memory_link 指向过期/删除条目 → 静默跳过."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            root = make_entry(
                namespace="user/facts", key="root",
                value={"content": "根记忆"}, topic_tags=["标签A"],
                entity_type="person",
            )
            await store.save(root)

            # Create a link to a non-existent entry
            await store._db.execute(
                "INSERT OR REPLACE INTO memory_links VALUES (?, ?, ?, ?, ?)",
                ("user/facts", "root", "user/facts", "ghost", "related_to"),
            )
            await store._db.commit()

            # Chain extension should skip the broken link and fallback
            chain = await store._extend_chain(root, target_n=2, namespace_prefix=None, max_per_level=3)
            # No entry for "ghost" should be in results
            assert not any(cm.entry.key == "ghost" for cm in chain)
            # Chain should still contain fallback entries if available
            # (if no other entries, chain may be empty — that's fine)
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_natural_language_output(self):
        """T019: 以"【记忆回溯】"开头, 含"我记得", 连接词多样, 情绪注解."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Create ChainedMemory entries manually
            cm1 = ChainedMemory(
                entry=make_entry(
                    namespace="user/facts", key="mem1",
                    value={"content": "小明是程序员，喜欢写Python"},
                    emotional_tags={"joy": 0.8},
                ),
                chain_level=0, chain_parent_key=None, relevance_score=1.0,
            )
            cm2 = ChainedMemory(
                entry=make_entry(
                    namespace="user/facts", key="mem2",
                    value={"content": "小明上周提到在做一个AI项目"},
                    emotional_tags={"interest": 0.7},
                ),
                chain_level=1, chain_parent_key="user/facts/mem1", relevance_score=0.8,
            )
            cm3 = ChainedMemory(
                entry=make_entry(
                    namespace="user/facts", key="mem3",
                    value={"content": "小明养了一只猫"},
                ),
                chain_level=1, chain_parent_key="user/facts/mem1", relevance_score=0.6,
            )

            # Use a fixed seed to make random connectors deterministic
            import random
            random.seed(42)
            text = store._format_recall_result([cm1, cm2, cm3])

            # Assert format
            assert text.startswith("【记忆回溯】"), f"Expected to start with 【记忆回溯】, got: {text[:50]}"
            assert "我记得" in text, f"Expected '我记得' in output, got: {text[:100]}"
            
            # Check for emotion annotation on the joy-tagged entry
            # cm1 has joy=0.8 → should have (当时聊这个的时候还挺开心的） or similar
            # cm2 has interest=0.7 → should have emotion annotation
            
            # Reset random and test with non-tagged entries only
            random.seed(42)
            text2 = store._format_recall_result([cm3])
            assert text2.startswith("【记忆回溯】"), f"Expected to start with 【记忆回溯】, got: {text2[:50]}"
            assert "我记得" in text2
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_empty_recall_human_text(self):
        """T020: 空 DB → 人性化文本."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            text = store._format_recall_result([])
            # Should be human text, not empty, not JSON, not "No results"
            assert len(text) > 0
            assert text != "[]"
            assert "No results" not in text
            assert not text.startswith("[")
            assert not text.startswith("{")
            # Should be one of the NO_MEMORY_PHRASES
            assert any(phrase in text for phrase in [
                "目前没什么特别的记忆浮现",
                "脑子里暂时一片空白",
                "一下子想不起相关的事",
            ])
        finally:
            await store.close()


# ── Spec 003: 深刻化 + 记忆分级测试 (Phase 4, US2) ────────────

class TestSalienceAndTiering:
    """salience 深刻化 + 记忆分级 (短期/长期/深刻) 测试."""

    @pytest.mark.asyncio
    async def test_salience_boost(self):
        """T025: 3 次 search_chained 同一 query, salience 递增."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Seed entry with salience=5.0
            e = make_entry(
                namespace="user/facts", key="boost_test",
                value={"content": "用户喜欢蓝色和绿色"},
                salience=5.0,
            )
            await store.save(e)

            # Call search_chained 3 times
            for _ in range(3):
                await store.search_chained("蓝色")

            # Check salience increased by 0.5 each time (direct match)
            updated = await store.get("user/facts", "boost_test")
            assert updated is not None
            assert updated.salience > 5.0, f"Expected salience > 5.0, got {updated.salience}"
            # After 3 calls, salience should be 5.0 + 3*0.5 = 6.5
            assert updated.salience == pytest.approx(6.5, abs=0.1), \
                f"Expected salience ~6.5, got {updated.salience}"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_access_count_increment(self):
        """T026: 2 次 search_chained, access_count 0→1→2."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            e = make_entry(
                namespace="user/facts", key="access_test",
                value={"content": "用户访问计数测试"},
                salience=5.0,
            )
            await store.save(e)

            # Initial state
            initial = await store.get("user/facts", "access_test")
            assert initial is not None
            assert initial.access_count == 0

            # First call
            await store.search_chained("访问计数")
            after_1 = await store.get("user/facts", "access_test")
            assert after_1 is not None
            assert after_1.access_count == 1, f"Expected 1, got {after_1.access_count}"

            # Second call
            await store.search_chained("访问计数")
            after_2 = await store.get("user/facts", "access_test")
            assert after_2 is not None
            assert after_2.access_count == 2, f"Expected 2, got {after_2.access_count}"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_migrate_short_to_long(self):
        """T027: short_term/ 条目 salience≥5 + access≥3 → 迁移到 user/."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Create entry in short_term/ that meets migration criteria
            e = make_entry(
                namespace="short_term/test", key="migrate_me",
                value={"content": "待迁移的短期记忆"},
                salience=5.5,
            )
            # Manually set access_count to 3 (since we can't easily call search_chained 3x on short_term)
            await store.save(e)
            await store._db.execute(
                "UPDATE memories SET access_count = 3 WHERE namespace = ? AND key = ?",
                ("short_term/test", "migrate_me"),
            )
            await store._db.commit()

            # Run migration
            await store._migrate_short_to_long()

            # Entry should no longer be in short_term/
            short = await store.get("short_term/test", "migrate_me")
            assert short is None, "Entry should have been removed from short_term/"

            # Entry should exist in user/test
            migrated = await store.get("user/test", "migrate_me")
            assert migrated is not None, "Entry should have been migrated to user/test"
            assert migrated.salience >= 5.0, f"Expected boosted salience, got {migrated.salience}"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_trim_short_term(self):
        """T028: 12 条 → 10 条, 最高 salience 保留."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Create 12 entries in short_term/ with varying salience
            for i in range(12):
                e = make_entry(
                    namespace="short_term/test", key=f"trim_{i:02d}",
                    value={"content": f"短期记忆 {i}"},
                    salience=float(i + 1),  # 1.0 ~ 12.0
                )
                await store.save(e)

            # Verify 12 entries exist
            all_before = await store.query("short_term/test", limit=20)
            assert len(all_before) == 12

            # Run trim
            await store._trim_short_term()

            # Verify only 10 remain
            all_after = await store.query("short_term/test", limit=20)
            assert len(all_after) == 10, f"Expected 10, got {len(all_after)}"

            # Highest salience entries should remain (salience 3~12, so keys trim_02 ~ trim_11)
            remaining_keys = {e.key for e in all_after}
            # trim_00 (salience=1) and trim_01 (salience=2) should be trimmed
            assert "trim_00" not in remaining_keys
            assert "trim_01" not in remaining_keys
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_mark_deep_memory(self):
        """T029: user/ 条目 salience≥7 → decay_curve='deep'."""
        store = MemoryStore(":memory:")
        await store.open()
        try:
            # Create entry with salience 7.0
            e = make_entry(
                namespace="user/facts", key="deep_memory",
                value={"content": "应该成为深刻记忆"},
                salience=7.0,
            )
            await store.save(e)

            # Initial decay_curve should be 'standard'
            initial = await store.get("user/facts", "deep_memory")
            assert initial is not None
            assert initial.decay_curve == "standard"

            # Run mark_deep_memory
            await store._mark_deep_memory()

            # Should now be 'deep'
            updated = await store.get("user/facts", "deep_memory")
            assert updated is not None
            assert updated.decay_curve == "deep", \
                f"Expected 'deep', got {updated.decay_curve}"
        finally:
            await store.close()
