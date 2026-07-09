"""MemoryStore — SQLite + FTS5 记忆存储（jieba 中文分词增强）"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from chat_core.core.types import MemoryEntry, MemoryLink, RelationType

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
        await self._db.commit()

    async def close(self) -> None:
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

    # ── CRUD ────────────────────────────────────────────────

    async def save(self, entry: MemoryEntry) -> None:
        assert self._db
        now = datetime.now().isoformat()
        entry.created_at = entry.created_at or datetime.now()
        entry.updated_at = datetime.now()
        await self._db.execute(
            """INSERT OR REPLACE INTO memories
               (namespace, key, value, layer, salience, entity_type, topic_tags,
                emotional_tags, created_at, updated_at, expires_at, ttl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        )
