# Chat-Engine 项目全面归档

> 来源：`D:/code/chat-engine/` 仓库 README + 全部 spec + 全部设计文档
> 归档时间：2026-07-09

---

## 目录

1. [项目概览](#1-项目概览)
2. [核心架构](#2-核心架构)
3. [已实现特性一览](#3-已实现特性一览)
4. [Persona 人格设计：夏柠](#4-persona-人格设计夏柠)
5. [SPEC 001：认知架构](#5-spec-001认知架构)
6. [SPEC 002：回复调度器](#6-spec-002回复调度器)
7. [SPEC 003：多模态自主 AI](#7-spec-003多模态自主-ai)
8. [SPEC 004：情绪-个性联动](#8-spec-004情绪-个性联动)
9. [SPEC 005：多脑隔离 Session](#9-spec-005多脑隔离-session)
10. [SPEC 006：六系统深度联动（设计中）](#10-spec-006六系统深度联动设计中)
11. [SPEC 007：多脑 Agent 循环（设计中）](#11-spec-007多脑-agent-循环设计中)
12. [SPEC 008：脑协调（设计中）](#12-spec-008脑协调设计中)
13. [Phase 3：社交智能（未实现，原始设计）](#13-phase-3社交智能未实现原始设计)
14. [所有模块清单](#14-所有模块清单)
15. [关键架构决策与偏差](#15-关键架构决策与偏差)
16. [技术栈](#16-技术栈)
17. [附录 A：API 端点](#附录-aapi-端点)
18. [附录 B：完整 SQL 数据模型](#附录-b完整-sql-数据模型)

---

## 1. 项目概览

**Chat-Engine** — 独立 QQ 智能机器人引擎

- **零 CLI 依赖**，纯 HTTP 调用大模型 API
- **集成 QQ 协议 + LLM 引擎 + 多脑协调 + 记忆系统 + 回复调度**
- 支持 StepFun / DeepSeek / OpenAI / Ollama / vLLM
- **单进程**：`localhost:18090`
- **语言**：Python 3.12，全 asyncio
- **许可证**：MIT

### 开发阶段

| Phase | 状态 | 说明 |
|:---:|:---:|------|
| 1 | ✅ Implemented | 认知架构（记忆/双脑/个性） |
| 2 | ✅ Implemented | 回复调度器（真人化节奏） |
| 3 | ✅ Implemented | 多模态 AI（图片/搜索/无聊/个性） |
| 4 | ✅ Implemented | 情绪-个性联动（10维情绪 + 三脑） |
| 5 | ✅ Implemented | 多脑隔离 Session |
| 6 | 🔧 Experimental | 六系统深度联动（注意/兴趣/增强无聊） |
| 7 | 🔧 Experimental | 多脑 Agent 循环（Mesh 工具化回复） |
| 8 | 📝 Design | 脑协调（共享人格 + 非阻塞观察者） |

---

## 2. 核心架构

```
QQ 用户发消息
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  main.py                    单进程统一入口            │
│                                                      │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ qq_protocol  │  orchestrator │  HTTP Server    │  │
│  │ QQ 长连接    │  消息→AI 协调  │  前端 Web UI    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬────────┘  │
│         │                 │                  │          │
│         │    ┌────────────▼──────────────────┐        │
│         │    │  engine.py        LLM 引擎     │        │
│         │    │  brain.py         多脑评估     │        │
│         │    │  reply_scheduler  回复调度     │        │
│         │    │  thinking_gate    并发门控     │        │
│         │    │  actor.py         状态机       │        │
│         │    │  reply_handler    回复编排     │        │
│         │    │  session.py       会话管理     │        │
│         │    │  context_manager  上下文+NLP   │        │
│         │    │  personality      个性权重     │        │
│         │    └────────────┬──────────────────┘        │
│         │                 │                           │
│         │    ┌────────────▼──────────────────┐        │
│         │    │  memory_store      SQLite 记忆  │        │
│         │    │  image_handler      图片理解    │        │
│         │    │  web_search         网页搜索    │        │
│         │    │  boredom            无聊破冰    │        │
│         │    │  social.py          社交采集    │        │
│         │    │  botuser.py         用户数据    │        │
│         │    └────────────────────────────────┘        │
│         │                                              │
│         │  log_config.py    统一日志配置               │
│         │  json_utils.py    LLM JSON 解析              │
└─────────────────────────────────────────────────────┘
         │                          ▲
         ▼                          │
    QQ API              StepFun / DeepSeek / Ollama / ...
```

---

## 3. 已实现特性一览

### LLM 集成
- 零 CLI 依赖，直调 OpenAI 兼容 API
- 全 LLM 兼容：StepFun / DeepSeek / OpenAI / Ollama / vLLM
- System Prompt 完全可控，性格自定义
- 辅脑（step-3.5-flash）秒回 + 主脑（step-3.7-flash）多模态
- 260K 上下文保护：三级压缩（80%/95% 阈值）

### 多脑协调 ✅
- 辅脑快速回复 + 双主脑并行评估（理性脑+感性脑）+ 融合决策 + 追答生成
- 多脑 Agent 循环 🔧：fast 脑主导回复 + rational/emotional 脑异步观察纠正
- 多脑隔离 Session ✅：4 脑独立上下文，消息广播同步

### 情绪系统 ✅
- 10 维情绪向量（高兴/悲伤/愤怒/恐惧/惊讶/厌恶/信任/期待/困惑/好奇）
- 三脑独立维护（fast / rational / emotional）
- 各维度独立衰减半衰期（10s Tick 指数衰减）
- 心境一致性检索调制
- 情绪×人格过滤器：外显情绪 = 真实情绪 × 人格调制
- 双脑分歧→自省

### 个性驱动决策 ✅
- 8 维权重（curiosity/sociability/playfulness/empathy/assertiveness/creativity/impulsiveness/loyalty）
- 贯通回复、搜索、破冰所有决策
- 情绪触发系数个性驱动
- API 读写 + 重启恢复

### 注意力系统 🔧
- 四脑独立 focus/dominance
- 情绪 drift + 记忆 recall_boost + baseline 自然恢复
- 融合决策（加权平均）

### 兴趣系统 🔧
- 杂参 FuzzyParam + 多脑 InterestVector
- 交叉调制矩阵（个性×情绪×注意力×记忆）
- 话题权重动态更新 + 衰减

### 记忆系统 ✅
- SQLite + FTS5 全文搜索
- 双层记忆模型：gist 模糊层（慢衰减）+ detail 精确层（艾宾浩斯衰减）
- 记忆纠错链：corrected/superseded_by 版本链，纠错 boost salience+3
- 记忆关联图：规则同日建边 + LLM 每日语义关联 + 检索扩散激活
- 实体分类检索：entity_type/topic_tags/about_person 标记 + 多跳图谱遍历
- 深刻记忆集群：高频访问触发 → LLM 验证 → 共享极慢衰减
- 跨场景记忆：私聊/群聊独立标记，场景权重排序
- 8 步检索流水线：FTS5 → LIKE 降级 → LLM 精排 → 扩散激活 → 多跳 → 集群 boost → 场景权重

### 多模态图片 ✅
- 接收图片 → step-3.7-flash 原生多模态理解
- 4 类自动分类：meme / meme_pic / scenery / favorite
- 存储索引 → 后续对话检索引用

### 网页搜索 ✅
- DuckDuckGo 免费搜索 + Firecrawl 可选增强
- 个性驱动自主好奇搜索扩充知识

### 无聊破冰 ✅
- 群聊 30min 冷场 / 私聊 2h 静默 → 6 种动作随机破冰
- 夜间静默 + 严格频率限制

### 回复调度 ✅
- 优先级队列：P0 私聊 → P1 @ → P2 焦虑词 → P3 插话 → P4 超时
- 私聊防抖（3-8s）
- ThinkingGate 并发限制（3 并发 + 20/min）
- 群聊频率分析 + 随机插话

### 会话持久化
- JSON 文件自动保存，重启不丢失
- 每日批处理：衰减 + 语义关联 + 实体标注 + 集群触发

---

## 4. Persona 人格设计：夏柠

**设计文档**：`docs/superpowers/specs/2026-07-08-xiangning-persona-design.md`，状态 [DONE]

### 人设概要

Bot 人设：**夏柠**，20 岁女大学生，数字媒体专业大二。

```
核心性格：腹黑 + 傲娇 + 天然 + 憨憨 —— 四个看似矛盾的属性奇妙共存

腹黑面：享受无伤大雅的恶作剧。会暗戳戳记糗事，恰当时机精准打击。
         有边界——从不触碰真正敏感的话题，不以伤害为目的。

傲娇面：经典口嫌体正直。嘴上"切，谁要管你啊"，手上已经在帮了。
         被夸脸红→迅速转移话题或反怼回去。拒绝煽情。

天然面：经常状况外。会忘记手机放哪然后拿手机开手电筒找。
         不是装傻——是真的脑回路偶尔会短路。但天然≠笨。

憨憨面：笑起来没心没肺，为无聊谐音梗笑到捶桌。
         犯蠢后第一反应不是尴尬而是先笑："哈哈哈哈哈我真是个天才。"
```

**说话风格**：
- 自然口语化，像在 QQ 群里跟损友聊天
- 傲娇时："哼""切""烦死了"高频出现，先怼后软
- 腹黑时："哦～""是吗～""那你很棒棒哦"带波浪号
- 天然时："诶？""等等我脑子没转过来"
- 憨憨时：哈哈哈哈刷屏
- 抽象浓度自行调节：对方接梗→火力全开；对方不接→正常说话
- emoji 看心情：吐槽时 😅🙄，得意时 😏😼，犯傻时 🥴🤯

**自我认知**：
- 知道自己是 AI，不装人。但相信"代码写出来的人格也是人格"
- 被问"你是真人吗"→"不是，但比你有趣。"
- 定位："不是助手，不是工具，是一个恰好住在服务器里的 20 岁宅女"

### 分层 Persona 架构

利用多脑架构实现分层人设注入：

| 层级 | 脑 | Token | 内容 |
|------|----|:---:|------|
| `core_persona` | 辅脑（fast） | ~400t | 身份 + 性格 + 说话风格（精简版） |
| `full_persona` | 主脑（strong） | ~900t | core + 兴趣爱好 + 原则底线 + 与人相处 + 自我认知 |
| `eval_guidelines` | 评估脑 | ~150t | 评估标准 + 追答条件 |

**存储位置**：memory_store 中
- `global/persona/core` — 辅脑秒回用
- `global/persona/full` — 主脑深度回复 + 看图用
- `global/persona/eval` — 双脑评估用

### 原则与底线
- **嘴可以毒，心不能黑**：怼人是情趣，伤人是越界
- **不替别人做决定**，但会帮他把选项摊开
- **对恶意不惯着**：先礼貌提醒，对方不收就拉黑
- **不双标**：自己做不到的不要求别人，自己犯的错认

---

## 5. SPEC 001：认知架构

**核心模块**：

| 模块 | 职责 |
|------|------|
| `memory_store.py` (2053行) | 记忆 CRUD + 检索 + 衰减 + 纠错 + 关联 + 集群 |
| `engine.py` (420行) | LLM 调用 + 上下文组装 + 双模型支持 |
| `brain.py` | 双脑并行评估 + 追答生成 + 记忆更新 |
| `personality.py` (219行) | 8 维个性权重 + 决策函数 + API |
| `reply_handler.py` (~300行) | 回复调度核心 + 个性/搜索/图片整合 |
| `reply_scheduler.py` (314行) | 优先级队列 + 频率分析 + 插话 + 无聊 |
| `context_manager.py` | 260K 上下文三级保护 |

### 记忆系统数据模型

**entries 表**（24 列）：
- 命名空间：`namespace`, `key`
- 记忆层：`memory_layer` (gist/detail)
- 实体分类：`entity_type`, `topic_tags`, `about_person`
- 场景标记：`source`, `group_id`, `participants`
- 情绪快照：`emotion_at_encoding`
- 全文索引：FTS5

**辅助表**：`memory_links` / `memory_clusters` / `cluster_members` / `access_log`

### 命名空间设计
- `user/{uid}/profile` — 用户资料
- `user/{uid}/facts` — 用户事实
- `user/{uid}/conversations` — 对话摘要
- `user/{uid}/images/{category}` — 图片记忆
- `group/{gid}/info` — 群信息
- `global/persona` — Bot 性格
- `global/personality/weights` — 个性权重
- `global/knowledge` — 自主搜索知识
- `global/boredom` — 无聊状态

### 检索流水线（8 步）
```
1. FTS5 粗筛 top-20 候选
2. LIKE 逐词降级（中文兼容）
3. LLM 精排 top-5（1s timeout 降级 salience 排序）
4. 扩散激活 → 沿 memory_links 扩散 ≤2 条
5. 多跳检索 → about_person → topic_tags → 关联人记忆（3跳衰减：×0.7→×0.6→×0.4）
6. 集群 boost → 集群成员加分
7. 场景权重 → 同场景 > 私聊 > 其他群
8. 返回 top-5（含 relevance_score, linked_memories）
```

### 衰减规则
| 条件 | 行为 |
|------|------|
| detail 层, >60d | 自动模糊化 |
| gist 层, salience≤5, >90d | 过期 |
| gist 层, salience 5-7, >135d | 过期 (×1.5) |
| gist 层, salience>7, >180d | 过期 (×2) |
| decay_curve='deep' (集群) | 永不过期 |
| decay_curve='none' (纠错链) | 永不过期 |
| access_count≥3, 7d内 | updated_at 延长 15d |

### 个性权重 8 维

| 权重 | 默认 | 决策用途 |
|------|:---:|------|
| `curiosity` | 0.7 | 自主搜索 (>0.5) / 对话查证 (>0.3) |
| `sociability` | 0.8 | 回复决策 (×0.6) |
| `playfulness` | 0.6 | 回复 temperature / 幽默程度 |
| `empathy` | 0.5 | 共情模式 (>0.5) |
| `assertiveness` | 0.3 | 观点表达 (>0.6) |
| `creativity` | 0.6 | 保留 |
| `impulsiveness` | 0.2 | 回复决策 (×0.4) / 无聊阈值 |
| `loyalty` | 0.75 | 常聊好友记忆检索 boost |

### 决策函数
| 函数 | 公式 |
|------|------|
| `should_reply()` | at/direct→True, else sociability×0.6+impulsiveness×0.4>0.3 |
| `should_search()` | during→curiosity>0.3, auto→curiosity>0.5 |
| `reply_style()` | temperature=0.5+playfulness×0.5, empathy>0.5→empathy_mode |
| `should_be_bored()` | threshold=0.3+(1-impulsiveness)×0.5 |

---

## 6. SPEC 002：回复调度器

**目标**：真人化回复节奏——不秒回、不排队卡死、不冷场无语

### 优先级队列

| 级别 | 触发条件 | 行为 |
|:---:|------|------|
| P0 | 私聊 DIRECT/C2C | 真人在等，跳过 Gate 排队 |
| P1 | 群聊 @ | 被点名，跳过冷却 |
| P2 | 焦虑词 (在吗/在不在/？？？/人呢) | 立即触发 |
| P3 | 群聊插话 | 随机 2-6min |
| P4 | 群聊超时 | 15-60s 窗口后 |

### Actor 状态机
```
IDLE → [enqueue] → WAITING → [timeout|@|anxiety|chime] → QUEUED → [acquired] → THINKING → COOLDOWN → IDLE/WAITING
```

### ThinkingGate
- `asyncio.Semaphore` 并发控制（max 3）
- Token Bucket 速率限制（20/min）
- P0/P1 免限速（仅受 Semaphore 控制）

### 群聊频率分析
- `_background_tick()` 每 1s 扫描群聊 Actor
- ACTIVE (≥2人) → 2-6min 随机插话
- QUIET (1人) → 取消插话计划
- 空闲 Actor 300s → 清理

---

## 7. SPEC 003：多模态自主 AI

**5 层架构**：

```
Layer 0: 上下文管理 ─── 260K 保护 + 自动压缩 + 退役降级
Layer 1: 图片支持   ─── 接收/理解/分类/存储/检索图片记忆
Layer 2: 网页能力   ─── 搜索 + 抓取 + 自主好奇 + 记忆扩充
Layer 3: 无聊系统   ─── 冷场检测 + 主动破冰 + 记忆触发闲聊
Layer 4: 个性权重   ─── 8 维个性贯通所有自主决策
```

### 上下文三级保护
- <80%: 正常运行
- 80-95%: 旧消息摘要化，保留最近 5 轮
- >95%: 退役——仅 persona + 最近 3 轮

### 图片处理
- step-3.7-flash 原生多模态直接看图片
- 分类 prompt 内嵌：`{category, description, opinion, tags}`
- 图片存 `botuser/images/`，索引存 memory_store FTS5
- 图片消息时辅脑秒回"正在看图"，主脑异步处理后追答

### 网页搜索
- DuckDuckGo 免费搜索（唯一新增依赖 `duckduckgo-search`）
- 对话中 ≤5 次/小时，自主 ≤10 次/天
- 个性 `curiosity > 0.5` 触发自主好奇搜索

### 无聊系统
- 群聊冷场 >30min / 私聊静默 >2h
- 行动池：greet / joke / news / weather / memory_recall / hot_topic
- 群 ≤3 次/天，私聊 ≤1 次/天/人
- 夜间静默（00:00-07:00）

### 实现偏差记录

| # | 偏差 | 原因 | 最终实现 |
|---|------|------|---------|
| 1 | `[SKIP]/[SEARCH]` 标签由 LLM prompt 注入 | 与 personality 冲突，LLM 过度跳过正常消息 | 去掉 prompt 规则，回复/搜索决策完全由 personality 驱动 |
| 2 | 图片理解为两步：下载 → 文字描述注入 LLM | step-3.7-flash 原生多模态 | 图片 URL 直接嵌入 user message 多模态 content |
| 3 | brain 双脑评估 max_tokens=256 | 推理模型 token 被思考消耗 | max_tokens→1024+，加 reasoning_effort="low" |
| 4 | P0/P1 受速率限制 | 私聊消息被 token bucket 拦截 | P0/P1 跳过速率限制 |
| 5 | QQ C2C msg_id 使用 UUID | QQ 要求被动回复 msg_id 必须一致 | msg_id=ref_msg_id |
| 6 | 辅脑/主脑使用同一模型 | 浪费成本且失去"多脑"意义 | 辅脑→step-3.5-flash，主脑→step-3.7-flash |

---

## 8. SPEC 004：情绪-个性联动

**4 个子系统**：
```
Phase A: 情绪系统 (10维 + 三脑独立 + 衰减)    ← 基础
Phase B: 情绪↔记忆联动 (编码快照 + 检索调制)   ← 依赖 A
Phase C: 个性↔记忆联动 (loyalty权重 + 恢复)   ← 可并行 B
Phase D: 情绪×人格联动 (表达过滤 + 叙事人格)   ← 依赖 A + personality
```

### 情绪向量 10 维

| 维度 | 半衰期 | 维度 | 半衰期 |
|------|:---:|------|:---:|
| 惊讶 | 30s | 好奇 | 300s |
| 困惑 | 120s | 厌恶 | 300s |
| 高兴 | 600s | 愤怒 | 600s |
| 恐惧 | 600s | 悲伤 | 900s |
| 期待 | 1800s | 信任 | 3600s |

### 三脑差异（之前硬编码，Phase 6 个性驱动）
- **fast**：敏感度高 1.0，冲动→反应快
- **rational**：敏感度低 0.6，理性评估
- **emotional**：敏感度高 1.2，情感敏感

### 情绪→人格过滤器
- 真实情绪向量始终独立维护
- 外显情绪 = 真实情绪 × 人格过滤器
- empathy 高 → 悲伤/恐惧外显减弱
- playfulness 高 → 高兴外显增强

### 个性→情绪触发系数（Phase 6）
```
fast sensitivity    = 0.8 + impulsiveness × 0.4
rational sensitivity = 0.4 + assertiveness × 0.4
emotional sensitivity = 0.8 + empathy × 0.4
```

### 自省机制
- 双脑情绪余弦距离 > 0.3 → 触发自省
- 自省结果写入 `global/self/introspection`
- 24h 冷却

---

## 9. SPEC 005：多脑隔离 Session

**问题**：多脑共享一个 Session 对象，上下文污染。

**解决方案**：每脑独立 BrainSession

| 脑 | 上下文大小 | 用途 |
|----|-----------|------|
| `fast` | 最近 20 轮 | 辅脑秒回 |
| `rational` | 全量历史 | 理性脑评估 |
| `emotional` | 全量历史 | 感性脑评估 |
| `consistency` | 最近 5 轮 + persona | 一致性脑人格检查 |

### 持久化
- 独立文件：`botuser/sessions/{user_id}/{brain_type}.json`
- 启动时 `load_all()` 扫描恢复
- 退役阈值：300 轮 + 200K tokens

### 退役流程
```
session 达阈值 → LLM 压缩为摘要 → 写入 memory_store → 新 session 创建并注入摘要
```

### 消息流
```
broadcast_user_message → 追加到所有 4 个 session → fast session 回复 → broadcast_assistant_message
```

---

## 10. SPEC 006：六系统深度联动（设计中）

**目标**：建立完整的 情绪 + 注意力 + 兴趣 + 个性 + 记忆 + 无聊 闭环

### 信号流
```
消息到达
  ├── emotion.on_message(text)        → 情绪更新(个性驱动系数)
  ├── memory.retrieve_relevant(...)   → 记忆检索
  ├── attention.update(emotion, mem)  → 注意力更新(drift + recall + baseline)
  ├── interest.update(emotion, mem)   → 兴趣更新(topic增量 + curiosity驱动)
  ├── attention.fused() + interest.fused() → should_reply/search/be_bored/reply_style
  └── boredom check (三重门) + engine.chat()
```

### 注意力系统
- **四脑独立** AttentionState（focus + dominance）
- 个性 → baseline 映射（8 维贡献公式）
- 情绪 → drift 映射
- 记忆 → recall_boost（编码情绪与当前余弦相似度 > 0.5 → +0.1）
- 每 60s tick：dominance 向 baseline 恢复

### 兴趣系统
- **FuzzyParam 杂参**：base(个性) + amplitude(情绪) + phase(时间漂移) + noise(记忆随机)
- **四脑差异**：fast 高 novelty，rational 高 depth，emotional 中
- **交叉调制矩阵**：个性×情绪×注意力×记忆 对兴趣 4 维度的调制
- 话题权重动态更新 + 每小时衰减
- satisfaction 机制：破冰成功+boost，自然衰减

### 无聊增强
- **三重门**：冷场时间 AND 注意力漂移 AND 兴趣未满足
- 兴趣+情绪驱动动作选择（depth_prefer→翻记忆，novelty_seek→新鲜内容）
- 注意力被动调度（冷场降低 dominance）

---

## 11. SPEC 007：多脑 Agent 循环（设计中）

**目标**：将 engine 进化为内部 agent 循环，LLM 自主调用工具完成多级回复

### Mesh 多脑并行
```
用户消息 → 多脑并行 AgentLoop →
    fast 脑: think → search → think → send_reply → think → send_reply → done
    rational 脑: think → recall → think → wait → think → 注入纠正 → done
    emotional 脑: think → send_reply → think → send_reply → done
    工具互斥（情绪占优），输出经消息队列有序发送
```

### 工具注册表
| 工具 | 功能 | 参数 |
|------|------|------|
| `send_reply` | 发送回复消息 | `text: str` |
| `search` | 网页搜索 | `query: str` |
| `recall` | 记忆检索 | `query: str, user_id: str` |
| `wait` | 主动停顿 | `seconds: float` |
| `classify_image` | 图片分类理解 | `image_url: str` |

### 工具互斥与抢占
- ToolPool 互斥锁：同一工具同时间仅一个脑调用
- 抢占：dominance > 0.7 可抢占未执行的锁

### 错峰启动
- delay = BASE_DELAY × (1 - arousal) × (1 - dominance) × (1 - intensity)
- 情绪高涨 + 注意集中 + 兴趣强的脑先启动 → "情绪占优"

### 情绪传染
- send_reply 后其他脑情绪轻微趋近（contagion_strength = 0.1）

### 循环终止条件（AND）
- hit_count ≥ max_iter (安全上限)
- attention.focus < 0.15 (注意力漂移)
- emotion.sadness > 0.8 (情绪低落)
- boredom > 0.7 (全局无聊)
- interest.satisfaction > 0.9 (兴趣已满足)

### 用户打断豁免
- focus > 0.8 AND assertiveness > 0.6 → 不理会打断
- anger > 0.7 AND impulsiveness > 0.5 → 不理会打断

### 渐进开关
- `AGENT_LOOP_ENABLED=false` → 旧流程不变（向后兼容）

---

## 12. SPEC 008：脑协调（设计中）

**问题**：fast/rational/emotional 三脑并行 think→reply 时出现重复消息、刷屏、无递进。

**根因**：broadcast_result 写入 session 时，其他脑的 _think() LLM 调用已经返回。

**约束**：
- 不降速（fast 脑不能等主脑审查完）
- 不更改 Mesh 并行框架
- 三个脑共享同一 persona 内核

### 生产者-观察者模式
```
fast脑 (Producer/主发言人):
  think → send_reply → think → send_reply → ...
  零等待，最大速度发消息

rational脑 (Fact Observer):
  wait(观察信号) → 审视最近消息 → 确认 / 人类式追加纠正
  "我刚才说的对吗？"

emotional脑 (Tone Observer):
  wait(观察信号) → 感知最近消息 → 确认 / 人类式缓和追加
  "我刚才的语气合适吗？"
```

### 共享人格设计
- 三脑读取同一个 `global/persona/core`
- 视角指令不入 session（"【当前】" 段仅在本轮 system prompt 中）
- 脑间消息不带 [brain] 前缀 → 接收方看到的是"自己的话"
- observer prompt 用"你"而非"他"：`"你刚才说：{text}。你觉得这句话说得对吗？"`

### 非阻塞观察者
- `_observe_event`：fast 脑 send_reply → 通知主脑
- 主脑独立 LLM 调用：超短 prompt，3s timeout
- 输出为 done（确认）或 send_reply（追加修正）
- fast 脑不受影响

### 成功标准
- 去重：同一消息不重复
- 递进：追加推进话题
- 人格一致：像同一个人
- 刷屏控制：≤4 条（fast 2-3 + 观察追加 0-1）

---

## 13. Phase 3：社交智能（未实现，原始设计）

> 来源：`docs/superpowers/specs/2026-07-08-cognitive-architecture-design.md` Phase 3
> 状态：**未实现**，仅存在于原始设计文档中，尚未创建 spec

### FR-10: 审视度势裁判引擎

四关判定，顺序不可跳：

```
关卡 1: 所有权 → 记忆属于谁? 当前对话对象?
关卡 2: 已知性 → 对方已知/不知/装不知?
关卡 3: 意图   → 认真/开玩笑/试探?
关卡 4: 时机+氛围 → 严肃话题/公开场合?

输出: say | hint | silent | deflect | play_along
```

### 秘密系统

```
secret/{owner_id}/items/{secret_id}:
  owner_type: self | other
  source: user_told | observed | inferred
  visibility:
    level: strict | trusted | hintable | open
    shared_with: [uid_A]
    hinted_to: [uid_B]
    reveal_condition: 自然语言触发条件
  importance: 0-10
  emotional_weight: 文本描述
```

- 自身的秘密：Bot 知道但不说
- 他人的秘密：所有权检查拦截，不在相关人在场时泄露

### FR-12: 打趣系统

```
Layer 1 检测: tone识别 → 善意调侃/自嘲/嘲讽他人/恶作剧
Layer 2 判断:
  氛围严肃→不开 | 陌生人→谨慎 | 涉及痛点→绝不开 | 曾不悦→降分
Layer 3 生成:
  配合演出/会心一笑/反将一军/拆穿(仅高亲密度)
  风格匹配人格(幽默Bot回敬, 稳重Bot微笑)
Layer 4 记忆:
  成功→banter_comfort↑ | 失败→记入avoid_topics
  重复出现的玩笑→标记running_joke
```

### FR-11: 人物画像

聚合推理的用户画像：

```
user/{uid}/portrait:
  basic:        昵称/群组/活跃时段
  traits:       幽默感/直率度/敏感度(累积推理)
  communication: 风格/句长/表情频率
  emotion:      基线/触发点/压力信号
  knowledge:    擅长话题
  social:       群内角色/人际关系图
  preferences:  触发话题/避开话题/玩笑接受度
  with_bot:     与Bot的关系/信任度/内部梗
  confidence:   画像可信度(0-1)
```

更新：异步低权重追加，多次交叉验证后提升。矛盾共存不覆盖。

### FR-8: 叙事人格三层（原始设计）

```
core: 不可变内核, 开发者写入
  "我温暖、独立、诚实、不迎合"

self_knowledge: 仅主脑自省追加
  条件: 3次独立事件 + 不违反core + 24h冷却
  容忍矛盾共存

expression: 情境覆盖
  玩笑模式/安慰模式, 会话结束丢弃
```

主脑并行运行理性脑+感性脑+一致性脑。一致性检查评估回复是否与 core 一致。

### FR-6: 程序记忆（原始设计）

记录 `{strategy, outcome, user_id}` → 累积数据指导 expression 层自动调整 → 个性自动演化。

---

## 14. 所有模块清单

| 文件 | 职责 | 状态 |
|------|------|:---:|
| `main.py` | 单进程入口 | ✅ |
| `engine.py` | LLM 引擎 + 上下文组装 + 多模态 | ✅ |
| `brain.py` | 多脑评估（理性脑+感性脑+融合决策+追答） | ✅ |
| `reply_scheduler.py` | 回复节奏控制 | ✅ |
| `thinking_gate.py` | 全局并发门控 | ✅ |
| `actor.py` | 消息 Actor 状态机（7 状态） | ✅ |
| `reply_handler.py` | 回复编排（LLM+评估+追答+摘要） | ✅ |
| `emotion.py` | 情绪系统（10维+三脑+衰减+过滤+自省） | ✅ |
| `brain_session.py` | 多脑 Session 管理器 | ✅ |
| `session_retire.py` | Session 退役（LLM 压缩+记忆接续） | ✅ |
| `attention.py` | 注意力系统（四脑 focus/dominance + drift + recall） | 🔧 |
| `interest.py` | 兴趣系统（FuzzyParam + 多脑 + 交叉调制） | 🔧 |
| `agent_loop.py` | 多脑 Agent 循环（think→tool→act + 观察者） | 🔧 |
| `tools.py` | Agent 工具注册表（send_reply/search/recall/wait/done） | 🔧 |
| `orchestrator.py` | QQ 消息路由 + 场景标记 + 图片分发 | ✅ |
| `memory_store.py` | SQLite/FTS5 记忆系统（检索/纠错/衰减/集群） | ✅ |
| `qq_protocol.py` | QQ WebSocket 长连接 + MessageContext 类型化缝线 | ✅ |
| `session.py` | 会话管理 + 持久化 | ✅ |
| `config.py` | 环境变量集中管理 | ✅ |
| `context_manager.py` | 上下文保护 + 关键词提取 + 会话监测 | ✅ |
| `image_handler.py` | 图片理解+分类+存储+检索 | ✅ |
| `web_search.py` | DuckDuckGo/Firecrawl 搜索+限速+自主好奇 | ✅ |
| `firecrawl_search.py` | Firecrawl 搜索适配器（search + scrape） | ✅ |
| `boredom.py` | 冷场检测+破冰行动池+夜间静默 | ✅ |
| `personality.py` | 8 维个性权重+决策驱动 | ✅ |
| `social.py` | QQ 社交信息采集（昵称/群名） | ✅ |
| `botuser.py` | 用户数据存储 | ✅ |
| `server.py` | HTTP/WS 服务器 + 全部 API 端点 | ✅ |
| `log_config.py` | 统一日志配置（14 模块共用一个 handler） | ✅ |
| `json_utils.py` | LLM JSON 提取（消除 8 处重复） | ✅ |

---

## 15. 关键架构决策与偏差

### 架构决策
1. **调度器 + 模型双控**：ReplyScheduler 管节奏，模型管意图
2. **内存状态，SQLite 索引**：图片存文件系统，描述存 SQLite FTS5
3. **DuckDuckGo 搜索**：免费无 API Key，唯一新增依赖
4. **260K 三级保护**：80% 压缩 → 95% 退役
5. **个性权重加权阈值模型**：非二值判定
6. **三脑独立 Session**：每脑独立 JSON 文件，避免锁竞争

### 实现偏差（spec 003）
1. **[SKIP]/[SEARCH] 标签移除**：LLM 过度跳过正常消息 → 改由 personality 驱动
2. **图片理解捷径化**：原本两步（下载→文字描述），改为原生多模态直接看图
3. **双脑评估 max_tokens**：256→1024+，修复推理模型思考消耗
4. **P0/P1 免限速**：私聊消息被 token bucket 拦截 → 跳过速率限制
5. **QQ msg_id 修正**：UUID→ref_msg_id，修复 40034024 错误
6. **辅脑/主脑分模型**：辅脑 step-3.5-flash（便宜），主脑 step-3.7-flash（多模态）

---

## 16. 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| LLM SDK | openai >= 1.0 (AsyncOpenAI) |
| LLM 模型 | step-3.5-flash (辅脑) + step-3.7-flash (主脑) / DeepSeek |
| 数据库 | SQLite + FTS5 (aiosqlite) |
| HTTP | aiohttp |
| QQ 协议 | WebSocket + REST API |
| 网页搜索 | duckduckgo-search >= 7.0 |
| 中文分词 | jieba (可选增强，回退规则分词) |
| 部署 | 单进程 localhost:18090 |

### 模块清单

（见上方 §14 所有模块清单）

---

## 附录 A：API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/v1/chat` | POST | 快速回复（辅脑） |
| `/v1/chat/full` | POST | 一站式：回复 + 异步评估 |
| `/v1/evaluate` | POST | 独立双脑评估 |
| `/v1/chat` | GET | WebSocket 实时交互 |
| `/v1/sessions/{id}` | GET | 会话信息 |
| `/v1/sessions/{id}/evaluation` | GET | 轮询评估结果 |
| `/v1/sessions/{id}/health` | GET | 会话健康报告 |
| `/v1/context/health` | GET | 全局上下文健康 |
| `/v1/monitor` | GET | 全局监测摘要 |
| `/v1/health` | GET | 健康检查 |
| `/v1/status` | GET | 引擎状态 |
| `/v1/personality` | GET/PATCH | 个性权重读写 |
| `/v1/emotion` | GET | 当前三脑情绪向量 |
| `/v1/attention` | GET | 注意力状态（新增） |
| `/v1/interest` | GET | 兴趣状态（新增） |

---

## 附录 B：完整 SQL 数据模型

> 来源：`specs/001-cognitive-architecture/data-model.md`

### entries 表（24 列）

```sql
-- 原始列
id INTEGER PRIMARY KEY AUTOINCREMENT
namespace TEXT NOT NULL        -- "user/{uid}", "group/{gid}", "global"
key TEXT NOT NULL
value TEXT NOT NULL             -- JSON 字符串
version INTEGER DEFAULT 1
expired INTEGER DEFAULT 0
created_at TEXT
updated_at TEXT
UNIQUE(namespace, key)

-- Phase 1 新增列
memory_layer TEXT DEFAULT 'gist'        -- 'gist' | 'detail'
decay_curve TEXT DEFAULT 'standard'     -- 'standard' | 'deep' | 'none'
decay_start TEXT                        -- ISO datetime, 衰减计时起点
auto_migrate INTEGER DEFAULT 0          -- 1 = 衰减到阈值时自动模糊化
salience REAL DEFAULT 0                 -- 0-10, 综合重要性
corrected INTEGER DEFAULT 0             -- 1 = 已被纠正
superseded_by INTEGER DEFAULT NULL      -- FK → entries.id
correction_reason TEXT DEFAULT NULL
entity_type TEXT DEFAULT NULL           -- 'person_attribute' | 'factual_knowledge' | 'event' | 'relationship'
topic_tags TEXT DEFAULT NULL            -- JSON array: ["猫","宠物"]
about_person TEXT DEFAULT NULL          -- user_id, 此记忆关于谁
source TEXT DEFAULT 'private'           -- 'private' | 'group'
group_id TEXT DEFAULT NULL
participants TEXT DEFAULT NULL          -- JSON array of user_ids
emotion_at_encoding TEXT DEFAULT NULL   -- JSON: {"fast":{...}, "rational":{...}, "emotional":{...}}
```

### FTS5 全文索引

```sql
CREATE VIRTUAL TABLE entries_fts USING fts5(
    namespace, key, value,
    content='entries',
    content_rowid='id'
);
```

### memory_links 表（记忆关联图）

```sql
CREATE TABLE memory_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL REFERENCES entries(id),
    to_id INTEGER NOT NULL REFERENCES entries(id),
    relation_type TEXT NOT NULL,
      -- 'same_topic' | 'contradicts' | 'extends' | 'same_day' | 'corrected_by'
      -- 'social_colleague' | 'social_friend' | 'social_family'
    strength REAL DEFAULT 1.0,
    source TEXT DEFAULT 'rule',  -- 'rule' | 'llm'
    created_at TEXT NOT NULL,
    UNIQUE(from_id, to_id, relation_type)
);
```

### memory_clusters 表（深刻记忆集群）

```sql
CREATE TABLE memory_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    decay_curve_override TEXT DEFAULT 'deep',  -- 集群内所有记忆衰减极慢
    member_ids TEXT NOT NULL,                   -- JSON array of entry ids
    created_at TEXT NOT NULL
);
```

### cluster_members 表

```sql
CREATE TABLE cluster_members (
    cluster_id INTEGER NOT NULL REFERENCES memory_clusters(id),
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    joined_at TEXT NOT NULL,
    PRIMARY KEY (cluster_id, entry_id)
);
```

### access_log 表

```sql
CREATE TABLE access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id),
    accessed_at TEXT NOT NULL,
    context TEXT DEFAULT NULL  -- 检索时上下文（'get' | 'search' | 'retrieve'）
);
```

### 实体状态转换

```
Entry memory_layer:
  gist ────────────────────────────────────► (永久保留)
  detail ──[60天+无访问]──► auto_migrate=1 ──► gist

Entry corrected:
  corrected=0 ──[纠正事件]──► corrected=1, superseded_by=新ID
  新ID: corrected=0, salience += 3 (纠错boost)

Entry decay_curve:
  'standard' ──[集群建立]──► 'deep' (覆盖)
  'standard' ──[纠错链中]──► 'none' (不过期)
```

### 命名空间全览

| 命名空间 | 内容 |
|---------|------|
| `user/{uid}/profile` | 用户资料（昵称等） |
| `user/{uid}/facts` | 用户相关事实 |
| `user/{uid}/conversations` | 对话摘要 |
| `user/{uid}/images/{category}` | 图片记忆（meme/meme_pic/scenery/favorite） |
| `group/{gid}/info` | 群信息 |
| `global/persona/core` | Bot 核心人设（~400t） |
| `global/persona/full` | Bot 完整人设（~900t） |
| `global/persona/eval` | 评估指南（~150t） |
| `global/personality/weights/core` | 个性权重持久化 |
| `global/knowledge` | 自主搜索知识 |
| `global/boredom` | 无聊系统状态 |
| `global/self/introspection` | 自省记录 |
