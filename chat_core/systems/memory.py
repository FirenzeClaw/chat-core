"""MemoryStore — SQLite + FTS5 记忆存储（jieba 中文分词增强）"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from chat_core.config import get_config
from chat_core.core.types import MemoryEntry, MemoryLink, RelationType, ChainedMemory, RecallChainConfig

# ── 衰减常量 ──────────────────────────────────────────────
DECAY_GIST_DAYS = 90
DECAY_DETAIL_DAYS = 60
AUTO_MIGRATE_DAYS = 30
ACCESS_BOOST_DAYS = 7
ACCESS_BOOST_MIN = 3

# 中文分词（可选依赖）
try:
    import jieba

    def _segment_chinese(text: str) -> list[str]:
        """用 jieba 分词，返回有意义的词列表（去重、去停用词）"""
        stop_words = {"的", "了", "是", "我", "你", "他", "她", "它", "们", "在", "有", "和", "都", "不", "也", "就", "要", "会", "吗", "呢", "吧", "啊", "哦", "嗯"}
        words = jieba.cut(text)
        return list(dict.fromkeys(w for w in words if len(w) >= 2 and w not in stop_words))
except ImportError:
    def _segment_chinese(text: str) -> list[str]:
        """无 jieba 时的降级：按 2-gram 切分"""
        return [text[i:i+2] for i in range(len(text)-1)] if len(text) >= 2 else [text]


class MemoryStore:
    """SQLite + FTS5 记忆存储，支持 CRUD、全文检索、命名空间隔离、关联

    T070: WAL 模式 + 并发安全 PRAGMAs 已启用。
    """

    def __init__(self, db_path: str | Path = "./data/memory.db"):
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._decay_task: asyncio.Task | None = None
        # ── Spec 003 §12: 幂律衰减配置 ──
        try:
            cfg = get_config()
            mc = cfg.memory_config()
            decay_cfg = mc.get("decay", {})
            self._decay_enabled: bool = bool(decay_cfg.get("enabled", True))
            self._decay_standard_beta: float = float(decay_cfg.get("standard_beta", 0.01))
            self._decay_deep_beta: float = float(decay_cfg.get("deep_beta", 0.001))
            self._decay_alpha: float = float(decay_cfg.get("alpha", 0.5))
            mig = decay_cfg.get("migration", {})
            self._migrate_up_threshold: float = float(mig.get("short_to_long_salience", 5))
            self._migrate_down_threshold: float = float(mig.get("long_to_short_salience", 3))
            self._deep_threshold: float = float(mig.get("deep_salience", 7))
            self._deep_fallback: float = float(mig.get("deep_fallback", 5))
            self._trim_short_max: int = int(decay_cfg.get("trim_short_max", 10))
        except Exception:
            # Config 不可用时使用默认值（测试环境常见）
            self._decay_enabled = True
            self._decay_standard_beta = 0.01
            self._decay_deep_beta = 0.001
            self._decay_alpha = 0.5
            self._migrate_up_threshold = 5.0
            self._migrate_down_threshold = 3.0
            self._deep_threshold = 7.0
            self._deep_fallback = 5.0
            self._trim_short_max = 10

    async def open(self) -> None:
        """打开数据库，创建表结构，启用 WAL 模式及并发安全设置"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        # T070: WAL 模式 + 并发安全 PRAGMAs
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")     # WAL 下 NORMAL 足够安全
        await self._db.execute("PRAGMA busy_timeout=5000")       # 5s 忙等待
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.execute("PRAGMA cache_size=-8000")        # 8MB 缓存
        await self._create_tables()
        await self._migrate_schema_003()
        await self._migrate_schema_decay()
        await self._migrate_schema_012()
        await self._db.commit()

    async def close(self) -> None:
        if self._decay_task:
            self._decay_task.cancel()
            self._decay_task = None
        if self._db:
            await self._db.close()
            self._db = None

    async def _create_tables(self) -> None:
        assert self._db
        # 主表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,         -- JSON
                layer TEXT DEFAULT 'gist',
                salience REAL DEFAULT 5.0,
                entity_type TEXT DEFAULT '',
                topic_tags TEXT DEFAULT '[]',  -- JSON array
                emotional_tags TEXT,           -- JSON or null
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                ttl INTEGER,
                PRIMARY KEY (namespace, key)
            )
        """)
        # FTS5 全文索引（列名必须与 memories 表一致）
        await self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                namespace, key, value, topic_tags, entity_type,
                content='memories', content_rowid='rowid'
            )
        """)
        # 迁移：修复 value_text → value 列名
        await self._migrate_fts_columns()
        # 关联表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memory_links (
                from_namespace TEXT NOT NULL,
                from_key TEXT NOT NULL,
                to_namespace TEXT NOT NULL,
                to_key TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY (from_namespace, from_key, to_namespace, to_key)
            )
        """)
        # 触发器：自动同步 FTS5
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, namespace, key, value, topic_tags, entity_type)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.topic_tags, new.entity_type);
            END
        """)
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, namespace, key, value, topic_tags, entity_type)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.topic_tags, old.entity_type);
            END
        """)
        await self._db.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, namespace, key, value, topic_tags, entity_type)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.topic_tags, old.entity_type);
                INSERT INTO memories_fts(rowid, namespace, key, value, topic_tags, entity_type)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.topic_tags, new.entity_type);
            END
        """)

    async def _migrate_fts_columns(self) -> None:
        """迁移：修复旧版 value_text 列名 → value（与主表列名一致）。
        
        FTS5 外部内容表要求列名与主表完全一致，否则 MATCH 查询回读主表
        时会报 'no such column: T.value_text'。
        """
        assert self._db
        # 检查 FTS 表是否使用旧列名 value_text
        cursor = await self._db.execute("PRAGMA table_info('memories_fts')")
        columns = [row[1] for row in await cursor.fetchall()]
        if "value_text" in columns:
            # 重建 FTS 表
            await self._db.execute("DROP TRIGGER IF EXISTS memories_ai")
            await self._db.execute("DROP TRIGGER IF EXISTS memories_ad")
            await self._db.execute("DROP TRIGGER IF EXISTS memories_au")
            await self._db.execute("DROP TABLE IF EXISTS memories_fts")
            await self._db.execute("""
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    namespace, key, value, topic_tags, entity_type,
                    content='memories', content_rowid='rowid'
                )
            """)
            # 从主表重建索引
            await self._db.execute(
                "INSERT INTO memories_fts(rowid, namespace, key, value, topic_tags, entity_type) "
                "SELECT rowid, namespace, key, value, topic_tags, entity_type FROM memories"
            )

    async def _migrate_schema_003(self) -> None:
        """Spec 003: 添加 access_count, last_access, decay_curve 列（含默认值，对现存行透明）"""
        assert self._db
        # 检查列是否已存在
        cursor = await self._db.execute("PRAGMA table_info('memories')")
        columns = {row[1] for row in await cursor.fetchall()}
        if "access_count" not in columns:
            await self._db.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
        if "last_access" not in columns:
            await self._db.execute("ALTER TABLE memories ADD COLUMN last_access TEXT")
        if "decay_curve" not in columns:
            await self._db.execute("ALTER TABLE memories ADD COLUMN decay_curve TEXT DEFAULT 'standard'")

    async def _migrate_schema_decay(self) -> None:
        """衰减系统: 添加 auto_migrate, decay_start 列"""
        assert self._db
        cursor = await self._db.execute("PRAGMA table_info('memories')")
        columns = {row[1] for row in await cursor.fetchall()}
        if "auto_migrate" not in columns:
            await self._db.execute("ALTER TABLE memories ADD COLUMN auto_migrate INTEGER DEFAULT 0")
        if "decay_start" not in columns:
            await self._db.execute("ALTER TABLE memories ADD COLUMN decay_start TEXT")

    async def _migrate_schema_012(self) -> None:
        """Spec 003 §12: 添加 created_at_epoch REAL 列用于幂律时间基准"""
        assert self._db
        cursor = await self._db.execute("PRAGMA table_info('memories')")
        columns = [row[1] for row in await cursor.fetchall()]
        if "created_at_epoch" not in columns:
            await self._db.execute(
                "ALTER TABLE memories ADD COLUMN created_at_epoch REAL DEFAULT (unixepoch())"
            )
            # 存量数据回填
            await self._db.execute(
                "UPDATE memories SET created_at_epoch = unixepoch(created_at) "
                "WHERE created_at_epoch IS NULL AND created_at IS NOT NULL"
            )
            await self._db.commit()

    # ── CRUD ────────────────────────────────────────────────

    async def save(self, entry: MemoryEntry) -> None:
        assert self._db
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or datetime.now()
        entry.updated_at = datetime.now()
        await self._db.execute(
            """INSERT OR REPLACE INTO memories
               (namespace, key, value, layer, salience, entity_type, topic_tags,
                emotional_tags, created_at, updated_at, expires_at, ttl,
                access_count, last_access, decay_curve, auto_migrate, decay_start,
                created_at_epoch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.namespace, entry.key,
                json.dumps(entry.value, ensure_ascii=False),
                entry.layer, entry.salience, entry.entity_type,
                json.dumps(entry.topic_tags, ensure_ascii=False),
                json.dumps(entry.emotional_tags, ensure_ascii=False) if entry.emotional_tags else None,
                entry.created_at.isoformat() if isinstance(entry.created_at, datetime) else entry.created_at,
                now,
                entry.expires_at.isoformat() if isinstance(entry.expires_at, datetime) and entry.expires_at else None,
                entry.ttl,
                entry.access_count,
                entry.last_access,
                entry.decay_curve,
                entry.auto_migrate,
                entry.decay_start,
                time.time() if entry.created_at_epoch is None else entry.created_at_epoch,
            ),
        )
        await self._db.commit()

    async def get(self, namespace: str, key: str) -> MemoryEntry | None:
        assert self._db
        cursor = await self._db.execute(
            "SELECT * FROM memories WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        row = await cursor.fetchone()
        return self._row_to_entry(row) if row else None

    async def delete(self, namespace: str, key: str) -> None:
        assert self._db
        await self._db.execute(
            "DELETE FROM memories WHERE namespace = ? AND key = ?",
            (namespace, key),
        )
        await self._db.commit()

    # ── 关联 ──────────────────────────────────────────────────

    async def link(self, from_ns: str, from_key: str, to_ns: str, to_key: str, relation: RelationType) -> None:
        assert self._db
        await self._db.execute(
            "INSERT OR REPLACE INTO memory_links VALUES (?, ?, ?, ?, ?)",
            (from_ns, from_key, to_ns, to_key, relation.value),
        )
        await self._db.commit()

    async def get_links(self, namespace: str, key: str, depth: int = 1) -> list[MemoryLink]:
        """获取关联记忆（支持扩散激活 depth 层）"""
        assert self._db
        result: list[MemoryLink] = []
        visited: set[tuple[str, str]] = {(namespace, key)}
        current = [(namespace, key)]

        for _ in range(depth):
            next_layer: list[tuple[str, str]] = []
            for ns, k in current:
                cursor = await self._db.execute(
                    "SELECT * FROM memory_links WHERE (from_namespace=? AND from_key=?) OR (to_namespace=? AND to_key=?)",
                    (ns, k, ns, k),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    link = MemoryLink(
                        from_key=f"{row['from_namespace']}/{row['from_key']}",
                        to_key=f"{row['to_namespace']}/{row['to_key']}",
                        relation=RelationType(row['relation']),
                    )
                    result.append(link)
                    # 双向追踪：链接两端都加入下一层扩散
                    from_pair = (row['from_namespace'], row['from_key'])
                    to_pair = (row['to_namespace'], row['to_key'])
                    for pair in (from_pair, to_pair):
                        if pair not in visited:
                            visited.add(pair)
                            next_layer.append(pair)
            current = next_layer

        return result

    # ── 情感标签 ──────────────────────────────────────────────

    async def tag(self, namespace: str, key: str, tags: dict[str, Any]) -> None:
        """给已有记忆追加情感标签（情感主脑专用）"""
        entry = await self.get(namespace, key)
        if entry is None:
            return
        existing = entry.emotional_tags or {}
        existing.update(tags)
        entry.emotional_tags = existing
        await self.save(entry)

    # ── 检索 ──────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        namespace_prefix: str | None = None,
        top_n: int = 20,
    ) -> list[MemoryEntry]:
        """FTS5 全文检索 → LIKE 降级 → spread activation (memory_links depth=2) → cluster boost (same topic_tags)"""
        assert self._db

        results = await self._search_fts5(query, namespace_prefix, top_n)
        if not results:
            results = await self._search_like(query, namespace_prefix, top_n)

        # 扩散激活：对 top 5 结果跟踪 memory_links (depth=2)
        results = await self._spread_activate(results, max_seeds=5, depth=2)

        # 聚类增强：有共同 topic_tags 的结果排前面
        results = self._cluster_boost(results)
        return results

    async def _search_fts5(self, query: str, namespace_prefix: str | None, top_n: int) -> list[MemoryEntry]:
        """FTS5 MATCH 检索，使用 jieba 分词增强中文查询"""
        assert self._db
        words = _segment_chinese(query)
        if not words:
            words = [query]

        # 用 OR 连接多个分词，每个词用双引号包裹做精确匹配
        safe_words = [f'"{w.replace(chr(34), chr(34)+chr(34))}"' for w in words]
        fts_query = " OR ".join(safe_words)

        sql = "SELECT namespace, key FROM memories_fts WHERE memories_fts MATCH ?"
        params: list[Any] = [fts_query]
        if namespace_prefix:
            sql += " AND namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {top_n}"
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except Exception:
            # FTS5 查询语法错误时降级到 LIKE
            return await self._search_like(query, namespace_prefix, top_n)

        entries: list[MemoryEntry] = []
        for row in rows:
            entry = await self.get(row["namespace"], row["key"])
            if entry:
                entries.append(entry)
        return entries

    async def _search_like(self, query: str, namespace_prefix: str | None, top_n: int) -> list[MemoryEntry]:
        """LIKE 模糊匹配降级，使用 jieba 分词做多关键词 OR 匹配"""
        assert self._db
        words = _segment_chinese(query)
        if not words:
            words = [query]

        # 用多个 LIKE 条件 OR 在一起
        like_clauses = " OR ".join(["value LIKE ?" for _ in words])
        sql = f"SELECT * FROM memories WHERE ({like_clauses})"
        params: list[Any] = [f"%{w}%" for w in words]
        if namespace_prefix:
            sql += " AND namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {top_n}"
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [self._row_to_entry(r) for r in rows if r]

    async def _spread_activate(
        self,
        seeds: list[MemoryEntry],
        max_seeds: int = 5,
        depth: int = 2,
    ) -> list[MemoryEntry]:
        """扩散激活：对 top-N 种子记忆追踪 memory_links，附带回关联条目。
        
        从种子记忆出发，沿 memory_links 扩散 depth 层，收集所有可达条目。
        直接查 links 表（避免复合 key 解析问题）。
        """
        if not seeds:
            return seeds

        seen: set[tuple[str, str]] = {
            (e.namespace, e.key) for e in seeds
        }
        linked_entries: list[MemoryEntry] = []
        current: list[tuple[str, str]] = [
            (e.namespace, e.key) for e in seeds[:max_seeds]
        ]

        for _ in range(depth):
            next_layer: list[tuple[str, str]] = []
            for ns, k in current:
                cursor = await self._db.execute(
                    "SELECT from_namespace, from_key, to_namespace, to_key "
                    "FROM memory_links "
                    "WHERE (from_namespace=? AND from_key=?) "
                    "   OR (to_namespace=? AND to_key=?)",
                    (ns, k, ns, k),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    for pair in (
                        (row[0], row[1]),  # from
                        (row[2], row[3]),  # to
                    ):
                        if pair not in seen and pair != (ns, k):
                            seen.add(pair)
                            next_layer.append(pair)
                            entry = await self.get(pair[0], pair[1])
                            if entry:
                                linked_entries.append(entry)
            current = next_layer

        return seeds + linked_entries

    def _cluster_boost(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        """聚类增强：统计 topic_tags 频率，将共享标签多的条目移到前面。
        
        不改变条目本身，只调整排序。无标签的条目保持原位。
        """
        if len(entries) <= 1:
            return entries

        # 统计每个 tag 的出现次数
        import json as _json
        tag_freq: dict[str, int] = {}
        for e in entries:
            try:
                tags = _json.loads(e.topic_tags) if e.topic_tags else []
            except Exception:
                tags = []
            for tag in tags:
                tag_freq[tag] = tag_freq.get(tag, 0) + 1

        if not tag_freq:
            return entries  # 无标签，无需重排

        # 计算每个条目的共享得分（其标签中最多出现次数）
        def _cluster_score(entry: MemoryEntry) -> int:
            try:
                tags = _json.loads(entry.topic_tags) if entry.topic_tags else []
            except Exception:
                return 0
            return max((tag_freq.get(t, 1) for t in tags), default=0)

        return sorted(entries, key=_cluster_score, reverse=True)

    # ── Spec 003: 记忆联锁 ──────────────────────────────────

    async def _extend_by_links(
        self, entry: MemoryEntry, remaining: int,
        max_per_level: int, namespace_prefix: str | None,
    ) -> list[ChainedMemory]:
        """L1: 从 memory_links 显式关联延伸"""
        assert self._db
        results: list[ChainedMemory] = []
        if remaining <= 0:
            return results

        take = min(remaining, max_per_level)
        sql = (
            "SELECT m.* FROM memories m "
            "JOIN memory_links ml ON "
            "  (ml.to_namespace = m.namespace AND ml.to_key = m.key) "
            "WHERE ml.from_namespace = ? AND ml.from_key = ? "
            "AND NOT (m.namespace = ? AND m.key = ?)"
        )
        params: list[Any] = [entry.namespace, entry.key, entry.namespace, entry.key]
        if namespace_prefix:
            sql += " AND m.namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {take}"

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        for row in rows:
            e = self._row_to_entry(row)
            if not self._is_expired(e):
                results.append(ChainedMemory(
                    entry=e, chain_level=1,
                    chain_parent_key=f"{entry.namespace}/{entry.key}",
                    relevance_score=0.8,
                ))
        return results

    async def _extend_by_tags(
        self, entry: MemoryEntry, remaining: int,
        max_per_level: int, namespace_prefix: str | None,
    ) -> list[ChainedMemory]:
        """L2: 按 topic_tags 交集延伸"""
        assert self._db
        results: list[ChainedMemory] = []
        if remaining <= 0 or not entry.topic_tags:
            return results

        take = min(remaining, max_per_level)
        # 构建 topic_tags LIKE 条件
        tag_conditions = " OR ".join(["m.topic_tags LIKE ?" for _ in entry.topic_tags])
        sql = (
            f"SELECT m.* FROM memories m WHERE ({tag_conditions}) "
            "AND NOT (m.namespace = ? AND m.key = ?)"
        )
        params: list[Any] = [f'%"{t}"%' for t in entry.topic_tags]
        params.extend([entry.namespace, entry.key])
        if namespace_prefix:
            sql += " AND m.namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {take}"

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        for row in rows:
            e = self._row_to_entry(row)
            if not self._is_expired(e):
                results.append(ChainedMemory(
                    entry=e, chain_level=2,
                    chain_parent_key=f"{entry.namespace}/{entry.key}",
                    relevance_score=0.6,
                ))
        return results

    async def _extend_by_entity(
        self, entry: MemoryEntry, remaining: int,
        max_per_level: int, namespace_prefix: str | None,
    ) -> list[ChainedMemory]:
        """L3: 按 entity_type 同类型延伸"""
        assert self._db
        results: list[ChainedMemory] = []
        if remaining <= 0 or not entry.entity_type:
            return results

        take = min(remaining, max_per_level)
        sql = (
            "SELECT m.* FROM memories m WHERE m.entity_type = ? "
            "AND NOT (m.namespace = ? AND m.key = ?)"
        )
        params: list[Any] = [entry.entity_type, entry.namespace, entry.key]
        if namespace_prefix:
            sql += " AND m.namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {take}"

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        for row in rows:
            e = self._row_to_entry(row)
            if not self._is_expired(e):
                results.append(ChainedMemory(
                    entry=e, chain_level=3,
                    chain_parent_key=f"{entry.namespace}/{entry.key}",
                    relevance_score=0.4,
                ))
        return results

    async def _extend_by_namespace(
        self, entry: MemoryEntry, remaining: int,
        max_per_level: int, namespace_prefix: str | None,
    ) -> list[ChainedMemory]:
        """L4: 按 namespace 同前缀延伸"""
        assert self._db
        results: list[ChainedMemory] = []
        if remaining <= 0:
            return results

        take = min(remaining, max_per_level)
        # 取 entry.namespace 的上层前缀 (如 user/uid → user/uid/%)
        ns_parts = entry.namespace.rsplit("/", 1)
        ns_prefix = ns_parts[0] + "/%" if len(ns_parts) > 1 else entry.namespace + "%"

        sql = (
            "SELECT m.* FROM memories m WHERE m.namespace LIKE ? "
            "AND NOT (m.namespace = ? AND m.key = ?)"
        )
        params: list[Any] = [ns_prefix, entry.namespace, entry.key]
        if namespace_prefix:
            sql += " AND m.namespace LIKE ?"
            params.append(f"{namespace_prefix}%")
        sql += f" LIMIT {take}"

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        for row in rows:
            e = self._row_to_entry(row)
            if not self._is_expired(e):
                results.append(ChainedMemory(
                    entry=e, chain_level=4,
                    chain_parent_key=f"{entry.namespace}/{entry.key}",
                    relevance_score=0.2,
                ))
        return results

    async def _extend_chain(
        self, entry: MemoryEntry, target_n: int,
        namespace_prefix: str | None, max_per_level: int,
    ) -> list[ChainedMemory]:
        """4 级 fallback 联锁延伸。断链静默跳过，自动降级。"""
        results: list[ChainedMemory] = []

        for level_fn in [
            self._extend_by_links,
            self._extend_by_tags,
            self._extend_by_entity,
            self._extend_by_namespace,
        ]:
            if len(results) >= target_n:
                break
            remaining = target_n - len(results)
            try:
                level_results = await level_fn(entry, remaining, max_per_level, namespace_prefix)
                results.extend(level_results)
            except Exception:
                # 断链静默跳过，自动降级到下一级
                pass

        return results[:target_n]

    def _dedup_by_quality(self, all_results: list[ChainedMemory]) -> list[ChainedMemory]:
        """全局去重：同 key 保留 chain_level 最小的（links > tags > entity > namespace）"""
        seen: dict[str, ChainedMemory] = {}
        for cm in all_results:
            key = f"{cm.entry.namespace}/{cm.entry.key}"
            if key not in seen or cm.chain_level < seen[key].chain_level:
                seen[key] = cm
        # 排序: chain_level (direct first) → relevance_score desc
        return sorted(seen.values(), key=lambda x: (x.chain_level, -x.relevance_score))

    # ── Spec 003: 自然语言回溯 ──────────────────────────────

    _CONNECTORS = [
        "还想起", "哦对", "说到这个",
        "这让我想起", "顺便一提", "对了",
        "说起来", "那次也是",
    ]

    _NO_MEMORY_PHRASES = [
        "目前没什么特别的记忆浮现。",
        "脑子里暂时一片空白。",
        "一下子想不起相关的事。",
    ]

    _EMOTION_DIMENSIONS = [
        "joy", "sadness", "anger", "fear", "surprise",
        "disgust", "trust", "anticipation", "interest", "confusion",
    ]

    _DERIVE_TEMPLATES: dict[str, list[str]] = {
        "joy": ["这些记忆让我觉得他最近状态不错。", "看来那段时间挺开心的。"],
        "sadness": ["这些记忆透着一丝感伤。", "能感觉到他有些低落。"],
        "anger": ["他似乎对某些事情耿耿于怀。"],
        "anxiety": ["这些记忆让我觉得他最近压力不小。", "能感觉到他有些焦虑。"],
        "surprise": ["有些出乎意料的事情发生在他身上。"],
        "trust": ["他对身边的人似乎很信任。"],
        "interest": ["他对新鲜事物保持着好奇。"],
    }

    def _summarize(self, cm: ChainedMemory) -> str:
        """将一条记忆摘要为 ≤100 字的简短文本"""
        e = cm.entry
        val = e.value
        if isinstance(val, dict):
            # 取第一个有意义的字符串值
            for v in val.values():
                if isinstance(v, str) and v.strip():
                    return v.strip()[:100]
            # fallback: JSON 摘要
            return json.dumps(val, ensure_ascii=False)[:100]
        return str(val)[:100]

    def _emotion_annotate(self, cm: ChainedMemory) -> str:
        """为有 emotional_tags 的记忆生成情绪注解"""
        e = cm.entry
        if not e.emotional_tags:
            return ""
        tags = e.emotional_tags
        # 找到最强的情绪维度
        strongest_dim = ""
        strongest_val = -1.0
        for dim in self._EMOTION_DIMENSIONS:
            val = tags.get(dim, 0)
            if isinstance(val, (int, float)) and val > strongest_val:
                strongest_val = float(val)
                strongest_dim = dim
        if strongest_dim and strongest_val > 0.3:
            annotations = {
                "joy": "当时聊这个的时候还挺开心的",
                "sadness": "说起这个有点伤感",
                "anger": "提到这事的时候能感觉到不满",
                "fear": "他好像有些担忧",
                "surprise": "当时挺意外的",
                "trust": "他对这事挺信任的",
                "anticipation": "他挺期待这个的",
                "interest": "他对这个很感兴趣",
                "confusion": "当时有点困惑",
            }
            text = annotations.get(strongest_dim, "")
            if text:
                return f"（{text}）"
        return ""

    def _time_annotate(self, cm: ChainedMemory) -> str:
        """Spec 007: 检测主观时间感知并生成注解"""
        e = cm.entry
        if not isinstance(e.value, dict):
            return ""
        stp = e.value.get("subjective_time_perception")
        if isinstance(stp, dict) and stp.get("perception") == "immersed":
            return "（那次聊得特别投入，时间过得飞快）"
        return ""

    def _derive_emotion_inference(self, entries: list[ChainedMemory]) -> str | None:
        """检测多条记忆的情绪一致性，生成推演"""
        emotions: list[str] = []
        for cm in entries:
            e = cm.entry
            if e.emotional_tags:
                for dim in self._EMOTION_DIMENSIONS:
                    val = e.emotional_tags.get(dim, 0)
                    if isinstance(val, (int, float)) and float(val) > 0.5:
                        emotions.append(dim)

        if len(emotions) < 2:
            return None

        # 找 dominant 维度
        from collections import Counter
        counts = Counter(emotions)
        total = len(emotions)
        dominant_dim, dominant_count = counts.most_common(1)[0]
        if dominant_count / total > 0.6:
            templates = self._DERIVE_TEMPLATES.get(dominant_dim)
            if templates:
                import random
                return random.choice(templates)

        return None

    def _format_recall_result(self, entries: list[ChainedMemory]) -> str:
        """将联锁记忆列表格式化为自然语言回溯文本"""
        import random

        if not entries:
            return random.choice(self._NO_MEMORY_PHRASES)

        lines: list[str] = []

        # 第一条: "我记得" + 情绪注解 + 时间注解
        direct = entries[0]
        summary = self._summarize(direct)
        annot = self._emotion_annotate(direct)
        time_annot = self._time_annotate(direct)
        line = f"我记得：{summary}。"
        if annot:
            line += annot
        if time_annot:
            line += time_annot
        lines.append(line)

        # 延伸记忆: 随机连接词 + 情绪注解 + 时间注解
        for cm in entries[1:]:
            connector = random.choice(self._CONNECTORS)
            summary = self._summarize(cm)
            annot = self._emotion_annotate(cm)
            time_annot = self._time_annotate(cm)
            line = f"{connector}：{summary}。"
            if annot:
                line += annot
            if time_annot:
                line += time_annot
            lines.append(line)

        # 情绪推演
        inference = self._derive_emotion_inference(entries)
        if inference:
            lines.append(inference)

        return "【记忆回溯】\n" + "\n".join(lines)

    # ── Spec 003: search_chained() ────────────────────────────

    async def search_chained(
        self,
        query: str,
        chain_config: RecallChainConfig | None = None,
    ) -> list[ChainedMemory]:
        """联锁记忆检索主入口。
        
        流程: FTS5 主检索 → 4 级延伸链 → 全局去重 → 深刻化 boost → 异步分级迁移。
        """
        assert self._db
        if chain_config is None:
            from chat_core.core.types import LOGIC_BRAIN_CHAIN_CONFIG
            chain_config = LOGIC_BRAIN_CHAIN_CONFIG

        ns_prefix = chain_config.namespace_prefix
        max_per_level = chain_config.max_per_level

        # ① FTS5 主检索
        directs = await self._search_fts5(query, ns_prefix, chain_config.top_n)
        if not directs:
            directs = await self._search_like(query, ns_prefix, chain_config.top_n)

        # ② 对每个 rank 延伸链
        all_chain: list[ChainedMemory] = []
        for rank, entry in enumerate(directs):
            ext_n = chain_config.extensions[rank] if rank < len(chain_config.extensions) else 0
            # Direct match
            all_chain.append(ChainedMemory(
                entry=entry, chain_level=0,
                chain_parent_key=None,
                relevance_score=1.0 - rank * 0.1,
            ))
            if ext_n > 0:
                extended = await self._extend_chain(entry, ext_n, ns_prefix, max_per_level)
                all_chain.extend(extended)

        # ③ 全局去重
        final = self._dedup_by_quality(all_chain)

        # ④ 深刻化: salience + access_count boost
        await self._apply_salience_boost(final)

        # ⑤ 异步触发记忆分级迁移 (双向)
        asyncio.create_task(self._migrate_short_to_long())
        asyncio.create_task(self._downgrade_long_to_short())
        asyncio.create_task(self._mark_deep_memory())
        asyncio.create_task(self._unmark_deep_memory())
        asyncio.create_task(self._trim_short_term())

        return final

    # ── Spec 003: 深刻化 + 记忆分级 ──────────────────────────

    _SALIENCE_BOOST = {
        0: 0.50,   # direct
        1: 0.30,   # links
        2: 0.20,   # topic_tags
        3: 0.15,   # entity
        4: 0.10,   # namespace
    }

    @staticmethod
    def effective_salience(
        salience: float,
        created_at_epoch: float | None,
        now_ts: float,
        decay_curve: str = "standard",
        standard_beta: float = 0.01,
        deep_beta: float = 0.001,
        alpha: float = 0.5,
        enabled: bool = True,
    ) -> float:
        """幂律衰减后的有效 salience。S / (1 + β × t^α)

        Args:
            salience: 原始 salience [0, 10]
            created_at_epoch: 创建时间 (unix timestamp)，None 则不衰减
            now_ts: 当前时间 (unix timestamp)
            decay_curve: "standard" | "deep" | "none"
            standard_beta: standard 曲线 β
            deep_beta: deep 曲线 β
            alpha: 曲率
            enabled: 全局开关
        """
        if not enabled or created_at_epoch is None or decay_curve == "none":
            return salience
        t_days = (now_ts - created_at_epoch) / 86400.0
        if t_days <= 0:
            return salience
        beta = deep_beta if decay_curve == "deep" else standard_beta
        return salience / (1.0 + beta * (t_days ** alpha))

    async def _apply_salience_boost(self, results: list[ChainedMemory]) -> None:
        """对所有命中记忆执行：幂律衰减 → salience boost → 硬上限。

        顺序: effective_salience() → 写回 DB → salience += chain_boost → MIN(10.0)
        decay.enabled=false 时跳过衰减，仅执行 boost（Spec 003 原始行为）。
        """
        assert self._db
        now_ts = time.time()
        now_iso = datetime.now().isoformat()
        for cm in results:
            e = cm.entry
            # ① 幂律衰减
            effective = self.effective_salience(
                e.salience,
                getattr(e, 'created_at_epoch', None),
                now_ts,
                getattr(e, 'decay_curve', 'standard'),
                self._decay_standard_beta,
                self._decay_deep_beta,
                self._decay_alpha,
                enabled=self._decay_enabled,
            )
            # ② boost
            boost = self._SALIENCE_BOOST.get(cm.chain_level, 0.10)
            new_salience = min(effective + boost, 10.0)
            # ③ 写回
            await self._db.execute(
                """UPDATE memories SET
                   salience = ?,
                   access_count = access_count + 1,
                   last_access = ?
                   WHERE namespace = ? AND key = ?""",
                (new_salience, now_iso, e.namespace, e.key),
            )
        await self._db.commit()

    async def _migrate_short_to_long(self) -> None:
        """短期记忆 → 长期记忆：salience ≥ 配置阈值 且 access_count ≥ 3 的迁移到 user/*"""
        assert self._db
        try:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE namespace LIKE ? AND salience >= ? AND access_count >= 3",
                ("short_term/%", self._migrate_up_threshold),
            )
            rows = await cursor.fetchall()
            for row in rows:
                entry = self._row_to_entry(row)
                old_ns = entry.namespace
                # 构造新 namespace: short_term/... → user/...
                new_ns = old_ns.replace("short_term/", "user/", 1)
                # 检查目标是否已存在
                existing = await self.get(new_ns, entry.key)
                if existing:
                    # 已存在则合并 salience
                    merged_salience = min(existing.salience + entry.salience * 0.5, 10.0)
                    await self._db.execute(
                        "UPDATE memories SET salience = ? WHERE namespace = ? AND key = ?",
                        (merged_salience, new_ns, entry.key),
                    )
                else:
                    entry.namespace = new_ns
                    entry.salience = min(entry.salience + 0.5, 10.0)
                    await self.save(entry)
                # 删除旧条目
                await self._db.execute(
                    "DELETE FROM memories WHERE namespace = ? AND key = ?",
                    (old_ns, entry.key),
                )
            if rows:
                await self._db.commit()
        except Exception:
            pass  # 静默失败，不影响主流程

    async def _downgrade_long_to_short(self) -> None:
        """长期记忆 → 短期记忆：salience < downgrade_threshold 的迁回 short_term/*。

        保留 access_count 和 last_access，不重置计数器。
        decay.enabled=false 时跳过。
        """
        if not self._decay_enabled:
            return
        assert self._db
        try:
            cursor = await self._db.execute(
                "SELECT * FROM memories WHERE namespace NOT LIKE ? AND salience < ?",
                ("short_term/%", self._migrate_down_threshold),
            )
            rows = await cursor.fetchall()
            for row in rows:
                entry = self._row_to_entry(row)
                old_ns = entry.namespace
                # 构造新 namespace: user/... → short_term/user/...
                new_ns = f"short_term/{old_ns}"
                # 不重置 access_count 和 last_access
                await self.save(MemoryEntry(
                    namespace=new_ns, key=entry.key,
                    value=entry.value, salience=entry.salience,
                    access_count=entry.access_count,
                    last_access=entry.last_access,
                    decay_curve=entry.decay_curve,
                    created_at=entry.created_at,
                    created_at_epoch=entry.created_at_epoch,
                ))
                await self._db.execute(
                    "DELETE FROM memories WHERE namespace = ? AND key = ?",
                    (old_ns, entry.key),
                )
            if rows:
                await self._db.commit()
        except Exception:
            pass

    async def _mark_deep_memory(self) -> None:
        """长期记忆 → 深刻记忆：salience ≥ 配置阈值 的标记 decay_curve='deep'"""
        assert self._db
        try:
            await self._db.execute(
                "UPDATE memories SET decay_curve = 'deep' "
                "WHERE salience >= ? AND decay_curve != 'deep' "
                "AND namespace NOT LIKE 'short_term/%'",
                (self._deep_threshold,),
            )
            await self._db.commit()
        except Exception:
            pass

    async def _unmark_deep_memory(self) -> None:
        """深刻记忆回退：salience < deep_fallback 的标记 decay_curve='standard'"""
        if not self._decay_enabled:
            return
        assert self._db
        try:
            await self._db.execute(
                "UPDATE memories SET decay_curve = 'standard' "
                "WHERE salience < ? AND decay_curve = 'deep' "
                "AND namespace NOT LIKE 'short_term/%'",
                (self._deep_fallback,),
            )
            await self._db.commit()
        except Exception:
            pass

    async def _trim_short_term(self) -> None:
        """short_term/* 裁剪至最多配置上限条（按 salience 降序保留）"""
        assert self._db
        try:
            cursor = await self._db.execute(
                "SELECT namespace, key, salience FROM memories "
                "WHERE namespace LIKE ? ORDER BY salience DESC",
                ("short_term/%",),
            )
            rows = await cursor.fetchall()
            if len(rows) > self._trim_short_max:
                for row in rows[self._trim_short_max:]:
                    await self._db.execute(
                        "DELETE FROM memories WHERE namespace = ? AND key = ?",
                        (row["namespace"], row["key"]),
                    )
                await self._db.commit()
        except Exception:
            pass

    # ── 衰减系统: 艾宾浩斯遗忘曲线 ──────────────────────────

    async def apply_decay(self) -> dict:
        """执行记忆衰减（艾宾浩斯遗忘曲线）。

        规则:
        - gist 层: 默认 90 天, 受 salience 调制延长
          · salience ≤ 5: 90 天过期
          · salience > 5 且 ≤ 7: 窗口延长 50% (135 天)
          · salience > 7: 窗口加倍 (180 天)
        - detail 层: >30 天 auto_migrate, >60 天过期
        - decay_curve='deep': 不衰减
        - 高频访问 boost: 7 天 ≥3 次 → decay_start 延长 15 天

        返回: {expired_count, boosted_count}
        """
        from datetime import timedelta

        assert self._db
        now = datetime.now()
        now_iso = now.isoformat()

        expired_count = 0
        boosted_count = 0

        # ── 1. detail 层衰减 ──
        # > AUTO_MIGRATE_DAYS 天 → 标记 auto_migrate=1
        cutoff_30 = (now - timedelta(days=AUTO_MIGRATE_DAYS)).isoformat()
        await self._db.execute(
            """UPDATE memories SET auto_migrate = 1
               WHERE layer = 'detail' AND decay_curve = 'standard'
               AND auto_migrate = 0
               AND created_at < ?""",
            (cutoff_30,),
        )

        # > DECAY_DETAIL_DAYS 天 且 auto_migrate=1 → 过期
        cutoff_60 = (now - timedelta(days=DECAY_DETAIL_DAYS)).isoformat()
        cursor = await self._db.execute(
            """UPDATE memories SET expires_at = ?
               WHERE (expires_at IS NULL OR expires_at > ?)
               AND layer = 'detail' AND auto_migrate = 1
               AND COALESCE(decay_start, created_at) < ?""",
            (now_iso, now_iso, cutoff_60),
        )
        expired_count += cursor.rowcount

        # ── 2. gist 层衰减 (salience 调制) ──
        # salience ≤ 5: 90 天
        cutoff_90 = (now - timedelta(days=DECAY_GIST_DAYS)).isoformat()
        cursor = await self._db.execute(
            """UPDATE memories SET expires_at = ?
               WHERE (expires_at IS NULL OR expires_at > ?)
               AND layer = 'gist' AND decay_curve = 'standard'
               AND salience <= 5
               AND COALESCE(decay_start, created_at) < ?""",
            (now_iso, now_iso, cutoff_90),
        )
        expired_count += cursor.rowcount

        # salience > 5 且 ≤ 7: 135 天
        cutoff_135 = (now - timedelta(days=int(DECAY_GIST_DAYS * 1.5))).isoformat()
        cursor = await self._db.execute(
            """UPDATE memories SET expires_at = ?
               WHERE (expires_at IS NULL OR expires_at > ?)
               AND layer = 'gist' AND decay_curve = 'standard'
               AND salience > 5 AND salience <= 7
               AND COALESCE(decay_start, created_at) < ?""",
            (now_iso, now_iso, cutoff_135),
        )
        expired_count += cursor.rowcount

        # salience > 7: 180 天
        cutoff_180 = (now - timedelta(days=DECAY_GIST_DAYS * 2)).isoformat()
        cursor = await self._db.execute(
            """UPDATE memories SET expires_at = ?
               WHERE (expires_at IS NULL OR expires_at > ?)
               AND layer = 'gist' AND decay_curve = 'standard'
               AND salience > 7
               AND COALESCE(decay_start, created_at) < ?""",
            (now_iso, now_iso, cutoff_180),
        )
        expired_count += cursor.rowcount

        # ── 3. deep 曲线: 永不衰减 (by design, no-op) ──

        # ── 4. 高频访问 boost ──
        cutoff_7d = (now - timedelta(days=ACCESS_BOOST_DAYS)).isoformat()
        cursor = await self._db.execute(
            """SELECT namespace, key, created_at, decay_start FROM memories
               WHERE decay_curve = 'standard'
               AND access_count >= ?
               AND last_access >= ?
               AND (expires_at IS NULL OR expires_at > ?)""",
            (ACCESS_BOOST_MIN, cutoff_7d, now_iso),
        )
        boost_rows = await cursor.fetchall()
        for row in boost_rows:
            base_str = row["decay_start"] or row["created_at"]
            try:
                base_dt = datetime.fromisoformat(base_str)
                new_start = (base_dt + timedelta(days=15)).isoformat()
                await self._db.execute(
                    "UPDATE memories SET decay_start = ? WHERE namespace = ? AND key = ?",
                    (new_start, row["namespace"], row["key"]),
                )
                boosted_count += 1
            except (ValueError, OSError):
                pass

        await self._db.commit()
        return {"expired_count": expired_count, "boosted_count": boosted_count}

    async def start_decay_tick(self, interval: int = 3600) -> None:
        """启动后台衰减定时任务（每小时执行一次 apply_decay）。

        调用方需持有 running event loop。任务在后台运行，store.close() 时取消。
        """
        async def _tick_loop() -> None:
            while self._db is not None:
                try:
                    await asyncio.sleep(interval)
                    if self._db is not None:
                        await self.apply_decay()
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass  # 静默失败，下次重试

        self._decay_task = asyncio.create_task(_tick_loop())

    async def query(
        self,
        namespace_prefix: str,
        limit: int = 10,
        include_expired: bool = False,
    ) -> list[MemoryEntry]:
        """按命名空间前缀查询，自动过滤过期和 TTL"""
        assert self._db
        now = datetime.now().isoformat()
        sql = "SELECT * FROM memories WHERE namespace LIKE ?"
        params: list[Any] = [f"{namespace_prefix}%"]

        if not include_expired:
            sql += " AND (expires_at IS NULL OR expires_at > ?)"
            params.append(now)

        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()

        entries = [self._row_to_entry(r) for r in rows if r]

        # TTL 过滤（在 Python 中计算）
        if not include_expired:
            entries = [e for e in entries if not self._is_expired(e)]

        return entries

    def _is_expired(self, entry: MemoryEntry) -> bool:
        """检查 TTL 是否过期"""
        if entry.ttl is None:
            return False
        if isinstance(entry.created_at, str):
            created = datetime.fromisoformat(entry.created_at).timestamp()
        else:
            created = entry.created_at.timestamp()
        return (time.time() - created) > entry.ttl

    # ── 辅助 ──────────────────────────────────────────────────

    def _row_to_entry(self, row: Any) -> MemoryEntry:
        return MemoryEntry(
            namespace=row["namespace"],
            key=row["key"],
            value=json.loads(row["value"]) if row["value"] else {},
            layer=row["layer"] or "gist",
            salience=float(row["salience"] or 5.0),
            entity_type=row["entity_type"] or "",
            topic_tags=json.loads(row["topic_tags"]) if row["topic_tags"] else [],
            emotional_tags=json.loads(row["emotional_tags"]) if row["emotional_tags"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
            ttl=int(row["ttl"]) if row["ttl"] else None,
            access_count=int(row["access_count"]) if row["access_count"] is not None else 0,
            last_access=row["last_access"] if row["last_access"] else None,
            decay_curve=row["decay_curve"] if row["decay_curve"] else "standard",
            auto_migrate=int(row["auto_migrate"]) if row["auto_migrate"] is not None else 0,
            decay_start=row["decay_start"] if row["decay_start"] else None,
            created_at_epoch=float(row["created_at_epoch"]) if row["created_at_epoch"] is not None else None,
        )
