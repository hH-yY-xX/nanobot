# Session Manager 会话管理器代码分析

## 代码概述

该模块实现了 Agent 的**会话管理系统**，负责对话历史的存储、加载和管理。采用 **JSONL (JSON Lines)** 格式进行持久化存储，支持高效的增量读写和 LLM 缓存优化。

### 核心职责
- 管理多个独立的对话会话
- 持久化存储对话历史
- 支持旧版本会话自动迁移
- 提供内存缓存加速访问

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                      SessionManager                             │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │   _cache     │    │ sessions_dir │    │ legacy_sessions  │  │
│  │  (内存缓存)   │    │ (工作区目录)  │    │   (旧版目录)     │  │
│  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘  │
│         │                   │                     │             │
│         └─────────┬─────────┴─────────────────────┘             │
│                   ▼                                              │
│           ┌───────────────┐                                     │
│           │    Session    │                                     │
│           │   (数据类)     │                                     │
│           └───────────────┘                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 存储层级

```
workspace/
└── sessions/
    ├── telegram_12345.jsonl
    ├── discord_67890.jsonl
    └── ...

~/.nanobot/sessions/  (旧版路径，自动迁移)
```

## 关键组件

### 1. Session 数据类

会话实体，存储单个对话的完整状态。

```python
@dataclass
class Session:
    key: str                    # 会话标识 (channel:chat_id)
    messages: list[dict]        # 消息列表
    created_at: datetime        # 创建时间
    updated_at: datetime        # 更新时间
    metadata: dict              # 元数据
    last_consolidated: int      # 已整合消息索引
```

### 2. SessionManager 类

会话管理器，负责会话的生命周期管理。

| 属性 | 类型 | 说明 |
|------|------|------|
| `workspace` | Path | 工作区根目录 |
| `sessions_dir` | Path | 会话存储目录 |
| `legacy_sessions_dir` | Path | 旧版会话目录 |
| `_cache` | dict | 内存会话缓存 |

## 数据结构

### JSONL 文件格式

```jsonl
{"_type": "metadata", "key": "telegram:123", "created_at": "...", "updated_at": "...", "metadata": {}, "last_consolidated": 10}
{"role": "user", "content": "你好", "timestamp": "2024-01-01T10:00:00"}
{"role": "assistant", "content": "你好！有什么可以帮你的？", "timestamp": "2024-01-01T10:00:05"}
{"role": "user", "content": "帮我查天气", "timestamp": "2024-01-01T10:01:00"}
...
```

### 消息结构

| 字段 | 必需 | 说明 |
|------|------|------|
| `role` | ✓ | 角色 (user/assistant/tool) |
| `content` | ✓ | 消息内容 |
| `timestamp` | ✓ | ISO 格式时间戳 |
| `tool_calls` | - | 工具调用列表 |
| `tool_call_id` | - | 工具调用 ID |
| `name` | - | 工具名称 |

### 元数据行结构

| 字段 | 说明 |
|------|------|
| `_type` | 固定值 "metadata" |
| `key` | 会话标识 |
| `created_at` | 创建时间 |
| `updated_at` | 更新时间 |
| `metadata` | 自定义元数据 |
| `last_consolidated` | 已整合消息数 |

## 核心流程

### 会话获取流程

```
┌──────────────────────┐
│  get_or_create(key)  │
└──────────┬───────────┘
           │
     ┌─────▼─────┐
     │ 检查缓存  │
     └─────┬─────┘
           │
      命中? ┼─ Yes ──► 返回缓存会话
           │
     ┌─────▼─────┐
     │ 从磁盘加载│
     └─────┬─────┘
           │
      存在? ┼─ Yes ──► 解析并缓存 ──► 返回
           │
     ┌─────▼─────┐
     │ 检查旧版  │
     │ 路径      │
     └─────┬─────┘
           │
      存在? ┼─ Yes ──► 迁移文件 ──► 解析并缓存 ──► 返回
           │
     ┌─────▼─────┐
     │ 创建新会话│
     └─────┬─────┘
           │
           ▼
       缓存并返回
```

### 历史消息获取流程

```
┌─────────────────────────────┐
│  get_history(max_messages)  │
└──────────────┬──────────────┘
               │
        ┌──────▼──────┐
        │ 截取未整合  │
        │ 消息        │
        │ [last_consolidated:]│
        └──────┬──────┘
               │
        ┌──────▼──────┐
        │ 限制消息数  │
        │ [-max:]     │
        └──────┬──────┘
               │
        ┌──────▼──────────┐
        │ 对齐到用户消息  │
        │ (跳过前导非user)│
        └──────┬──────────┘
               │
        ┌──────▼──────┐
        │ 提取 LLM    │
        │ 所需字段    │
        └──────┬──────┘
               │
               ▼
           返回结果
```

## 核心方法详解

### Session 类方法

#### `add_message(role, content, **kwargs)`
添加消息到会话，自动记录时间戳。

```python
def add_message(self, role: str, content: str, **kwargs: Any) -> None:
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat(),
        **kwargs  # 支持 tool_calls, tool_call_id 等
    }
    self.messages.append(msg)
    self.updated_at = datetime.now()
```

#### `get_history(max_messages=500)`
获取未整合的消息用于 LLM 输入。

**关键逻辑:**
1. 从 `last_consolidated` 位置开始截取
2. 限制最大消息数
3. 跳过前导非用户消息（避免孤立的 tool_result）
4. 只提取 LLM 需要的字段

#### `clear()`
清空会话，重置所有状态。

### SessionManager 类方法

#### `get_or_create(key)`
获取或创建会话，优先使用缓存。

#### `_load(key)`
从磁盘加载会话，支持旧版路径迁移。

**旧版迁移逻辑:**
```python
if not path.exists():
    legacy_path = self._get_legacy_session_path(key)
    if legacy_path.exists():
        shutil.move(str(legacy_path), str(path))  # 自动迁移
```

#### `save(session)`
保存会话到磁盘，更新缓存。

**写入格式:**
1. 第一行: 元数据行 (`_type: "metadata"`)
2. 后续行: 每条消息一行

#### `list_sessions()`
列出所有会话，只读取元数据行以提高效率。

## 设计亮点

### 1. Append-Only 消息策略

```
消息只追加不修改 → 保证 LLM 缓存命中率
                → 避免前缀 token 重新计算
```

### 2. 分层整合机制

```
┌─────────────────────────────────────────────┐
│           messages[] (完整历史)             │
├─────────────────────────────────────────────┤
│  [0 ... last_consolidated] │ [未整合部分]   │
│       ↓                    │       ↓        │
│   已写入 MEMORY.md         │  用于 LLM 输入 │
│   已写入 HISTORY.md        │               │
└─────────────────────────────────────────────┘
```

### 3. 智能历史对齐

```python
# 跳过前导非用户消息，确保历史从用户消息开始
for i, m in enumerate(sliced):
    if m.get("role") == "user":
        sliced = sliced[i:]
        break
```

### 4. 高效列表查询

```python
# 只读取第一行元数据，不加载全部消息
with open(path) as f:
    first_line = f.readline()
```

## 设计模式

| 模式 | 应用场景 |
|------|----------|
| **Repository 模式** | SessionManager 封装数据访问逻辑 |
| **缓存模式** | `_cache` 字典缓存已加载会话 |
| **工厂模式** | `get_or_create()` 统一创建入口 |
| **数据类模式** | `@dataclass` 简化 Session 定义 |
| **迁移模式** | 自动迁移旧版数据到新位置 |

## 依赖关系

```
┌─────────────────────────────────────────┐
│            manager.py                   │
├─────────────────────────────────────────┤
│  内部依赖:                              │
│  ├─ nanobot.config.paths               │
│  │   └─ get_legacy_sessions_dir()      │
│  └─ nanobot.utils.helpers              │
│      ├─ ensure_dir()                   │
│      └─ safe_filename()                │
├─────────────────────────────────────────┤
│  外部依赖:                              │
│  ├─ json (序列化)                       │
│  ├─ shutil (文件迁移)                   │
│  ├─ dataclasses (数据类)                │
│  ├─ datetime (时间处理)                 │
│  ├─ pathlib (路径操作)                  │
│  └─ loguru (日志记录)                   │
└─────────────────────────────────────────┘
```

## 潜在改进建议

### 1. 并发安全
当前实现非线程安全，多线程访问同一会话可能导致数据竞争。

**建议:** 添加文件锁或使用异步锁机制。

### 2. 增量保存
当前 `save()` 每次重写整个文件，消息量大时效率低。

**建议:** 实现增量追加写入模式。

```python
def append_message(self, session: Session, msg: dict) -> None:
    path = self._get_session_path(session.key)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
```

### 3. 缓存过期
当前缓存无过期机制，长时间运行可能占用大量内存。

**建议:** 添加 LRU 缓存或定时清理机制。

### 4. 会话压缩
历史会话文件可能很大，占用磁盘空间。

**建议:** 对旧会话进行 gzip 压缩。

### 5. 索引支持
`list_sessions()` 需要遍历所有文件。

**建议:** 维护一个索引文件，加速会话列表查询。

### 6. 事务支持
当前保存操作非原子性，写入中断可能导致数据损坏。

**建议:** 使用临时文件 + 重命名的原子写入模式。

```python
def save(self, session: Session) -> None:
    path = self._get_session_path(session.key)
    temp_path = path.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        # 写入内容...
    temp_path.rename(path)  # 原子操作
```

### 7. 备份机制
误操作可能丢失重要对话历史。

**建议:** 定期备份或保留历史版本。
