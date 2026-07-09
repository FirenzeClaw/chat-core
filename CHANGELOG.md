# Changelog

## [0.1.0] — 2026-07-09

### Added

- 四脑架构：逻辑主脑、情感主脑、子Session（嘴）、行为脑
- ReAct Loop 引擎：think → act → observe 循环，支持 function calling
- 子Session 工具：send_reply、wait、recall、inner_thoughts（调用即结束）、done
- MemoryStore：SQLite + FTS5 全文检索 + jieba 中文分词
- 三层记忆：清醒记忆、短期记忆、潜意识区
- EmotionEngine：10 维情绪 × 3 脑，独立衰减半衰期 + 脑间传染
- PersonalityEngine：8 维人格权重 → 行为参数映射
- BoredomDetector：指数衰减无聊检测 + 主动发起
- InterestModel：话题追踪 + FuzzyParam 模糊化触发
- ReviewSystem：三层错误检测（关键词 → 缓存 → LLM）
- 权重决策：logic × 0.6 + emotion × 0.4，阈值 0.5
- 沉默累积器：FuzzyParam 非机械"忍无可忍"触发
- 拧巴记录：逻辑与情感分歧时按逻辑执行并记录
- 意图提取：regex → 关键词 → LLM fallback
- 行为脑并发池：Semaphore(2) + Token Bucket 限速
- 上下文压缩：三级策略（70%/85% 阈值）
- 子Session 退役机制
- Rich TUI：prompt_toolkit + rich，情绪状态栏、打字动画
- 多模态降级链：图片 → vision model → fallback → 文本描述
- 安全过滤：ContentFilter 内容检测 + 长度截断 + URL 白名单
- 对话历史 JSONL 持久化
- 配置 schema 校验 + .env 自动加载
- 84 单元测试 + 19 Spec E2E 测试
