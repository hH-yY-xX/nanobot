# Nanobot 记忆系统分析报告

## 代码概述

`memory.py` 实现了 nanobot 的持久化记忆系统，核心是 `MemoryStore` 类。该系统采用**双层记忆架构**：
- **MEMORY.md** — 长期事实记忆（结构化知识）
- **HISTORY.md** — 可搜索的历史日志（grep 友好的时间序列）

系统通过 LLM 工具调用机制实现**智能记忆整合**，将对话历史自动提炼为持久化知识。

---

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      AgentLoop                               │
│  ┌─────────────────┐    ┌─────────────────┐                 │
│  │ SessionManager  │    │  ContextBuilder │                 │
│  │ (会话消息管理)   │    │  (上下文构建)    │                 │
│  └────────┬────────┘    └────────┬────────┘                 │
│           │                      │                           │
│           │ last_consolidated    │ get_memory_context()     │
│           ▼                      ▼                           │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    MemoryStore                           ││
│  │  ┌─────────────┐        ┌─────────────┐                 ││
│  │  │ MEMORY.md   │        │ HISTORY.md  │                 ││
│  │  │ (长期记忆)   │        │ (历史日志)   │                 ││
│  │  └─────────────┘        └─────────────┘                 ││
│  └─────────────────────────────────────────────────────────┘│
│                              ▲                               │
│                              │ consolidate()                 │
│                              │                               │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    LLMProvider                           ││
│  │              (记忆整合的 AI 推理引擎)                      ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## 关键组件

### 1. MemoryStore 类

核心记忆存储类，负责文件读写和整合协调：

| 方法 | 功能 |
|------|------|
| `read_long_term()` | 读取 MEMORY.md 内容 |
| `write_long_term()` | 写入长期记忆 |
| `append_history()` | 追加历史条目 |
| `get_memory_context()` | 获取格式化记忆上下文 |
| `consolidate()` | 执行记忆整合（核心方法） |

### 2. save_memory 工具定义

```python
_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]
```

**关键字段说明：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `history_entry` | string | 是 | 2-5句话的事件摘要，以 `[YYYY-MM-DD HH:MM]` 开头，便于 grep 搜索 |
| `memory_update` | string | 是 | 完整的 Markdown 格式长期记忆，包含所有已有事实和新事实 |

---

## 数据结构

### 文件存储结构

```
workspace/
└── memory/
    ├── MEMORY.md      # 长期事实记忆
    └── HISTORY.md     # grep 可搜索历史日志
```

### Session 与 MemoryStore 的关联

```python
@dataclass
class Session:
    key: str                    # 会话标识 (channel:chat_id)
    messages: list[dict]        # 消息列表
    last_consolidated: int      # 已整合消息的索引位置
```

### 消息格式（整合输入）

```
[2024-01-15 14:30] USER: 请帮我搜索天气
[2024-01-15 14:30] ASSISTANT [tools: web_search]: 正在搜索...
```

---

## 核心流程

### 记忆整合触发流程

```
┌─────────────────────────────────────────────────────────────┐
│                    消息处理流程                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│ unconsolidated = len(messages) - last_consolidated         │
│                                                             │
│ if unconsolidated >= memory_window:                        │
│     启动后台整合任务                                         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  MemoryStore.consolidate()                  │
│                                                             │
│ 1. 获取待整合消息 (last_consolidated : -keep_count)         │
│ 2. 格式化为带时间戳的文本                                    │
│ 3. 构建 LLM 整合 Prompt                                     │
│ 4. 调用 LLM 执行 save_memory 工具                           │
│ 5. 解析工具参数并写入文件                                    │
│ 6. 更新 session.last_consolidated                           │
└─────────────────────────────────────────────────────────────┘
```

### /new 命令归档流程

```
/new 命令
    │
    ▼
获取会话锁 (防止并发)
    │
    ▼
快照 session.messages[last_consolidated:]
    │
    ▼
consolidate(archive_all=True)  # 强制归档全部
    │
    ▼
session.clear()  # 清空会话
    │
    ▼
返回 "New session started."
```

---

## 核心方法详解

### consolidate() 方法

```python
async def consolidate(
    self,
    session: Session,
    provider: LLMProvider,
    model: str,
    *,
    archive_all: bool = False,    # True: 归档全部，False: 增量整合
    memory_window: int = 50,      # 保留窗口大小
) -> bool:
```

**关键逻辑：**

1. **消息选取策略**
   - `archive_all=True`: 整合所有消息 (`keep_count = 0`)
   - `archive_all=False`: 保留 `memory_window // 2` 条最新消息，整合其余
   
   **提前返回条件：**
   - 消息总数 ≤ `keep_count`：无需整合
   - `len(messages) - last_consolidated <= 0`：无新消息需要整合
   - `old_messages` 为空：无可整合内容

2. **消息格式化**
   ```python
   lines.append(f"[{timestamp[:16]}] {role.upper()}{tools}: {content}")
   ```

3. **LLM Prompt 构建**
   ```
   ## Current Long-term Memory
   {当前 MEMORY.md 内容}
   
   ## Conversation to Process
   {格式化的对话记录}
   ```

4. **工具调用结果解析**
   
   兼容多种 Provider 返回格式：
   ```python
   args = response.tool_calls[0].arguments
   if isinstance(args, str):
       args = json.loads(args)  # JSON 字符串转 dict
   if isinstance(args, list):
       if args and isinstance(args[0], dict):
           args = args[0]  # 取列表第一个元素
       else:
           return False  # 空列表或非 dict 列表视为失败
   if not isinstance(args, dict):
       return False  # 非 dict 类型视为失败
   ```
   
   - 支持 `dict` 和 JSON 字符串两种格式
   - 处理某些 provider 返回 `list` 的边缘情况
   - 严格的类型检查确保数据安全

5. **文件写入与类型处理**
   
   ```python
   if entry := args.get("history_entry"):
       if not isinstance(entry, str):
           entry = json.dumps(entry, ensure_ascii=False)
       self.append_history(entry)
   if update := args.get("memory_update"):
       if not isinstance(update, str):
           update = json.dumps(update, ensure_ascii=False)
       if update != current_memory:  # 仅当内容变化时才写入
           self.write_long_term(update)
   ```
   
   - `history_entry` → 追加到 HISTORY.md
   - `memory_update` → 覆盖 MEMORY.md（仅当内容变化时）
   - 非字符串值自动转换为 JSON 字符串

### 其他方法

#### get_memory_context()

```python
def get_memory_context(self) -> str:
    long_term = self.read_long_term()
    return f"## Long-term Memory\n{long_term}" if long_term else ""
```

在 `ContextBuilder.build_system_prompt()` 中被调用，将长期记忆注入系统提示。

#### 文件操作方法

| 方法 | 功能 | 实现细节 |
|------|------|----------|
| `read_long_term()` | 读取 MEMORY.md | 文件不存在返回空字符串，UTF-8 编码 |
| `write_long_term(content)` | 写入长期记忆 | 覆盖写入，UTF-8 编码 |
| `append_history(entry)` | 追加历史条目 | 追加模式写入，自动添加换行 |

---

## 设计亮点

### 1. 双层记忆分离

| 层级 | 文件 | 特点 | 用途 |
|------|------|------|------|
| 长期记忆 | MEMORY.md | 结构化、可更新 | 持久知识存储 |
| 历史日志 | HISTORY.md | 追加式、时间序列 | grep 搜索、审计 |

### 2. LLM 驱动的智能整合

- 不是简单的消息截断，而是通过 LLM 理解对话语义
- 自动提取关键信息更新长期记忆
- 生成 grep 友好的历史摘要

### 3. 非阻塞异步整合

```python
# loop.py 中的后台任务模式
asyncio.create_task(_consolidate_and_unlock())
```

- 整合在后台执行，不阻塞用户交互
- 使用 `_consolidating` 集合防止重复触发
- 使用 `asyncio.Lock` 保证同一会话串行整合

### 4. Append-Only 消息设计

```python
# Session 类注释
# Important: Messages are append-only for LLM cache efficiency.
# The consolidation process writes summaries to MEMORY.md/HISTORY.md
# but does NOT modify the messages list or get_history() output.
```

- 消息列表只追加不删除，保持 LLM 缓存效率
- 通过 `last_consolidated` 指针控制已整合范围
- `get_history()` 仅返回未整合部分

### 5. 健壮的参数解析

```python
# 兼容多种 provider 返回格式
args = response.tool_calls[0].arguments
if isinstance(args, str):
    args = json.loads(args)
if isinstance(args, list):
    if args and isinstance(args[0], dict):
        args = args[0]
    else:
        logger.warning("unexpected arguments as empty or non-dict list")
        return False
if not isinstance(args, dict):
    logger.warning("unexpected arguments type {}", type(args).__name__)
    return False
```

- 三层类型检查：字符串 → 列表 → 字典
- 详细的警告日志便于问题定位
- 严格的失败处理避免脏数据写入

---

## 设计模式

### 1. 策略模式 (Strategy Pattern)

通过 `LLMProvider` 抽象接口，支持不同的 AI 后端（OpenAI、Azure、LiteLLM 等）执行记忆整合。

### 2. 工具调用模式 (Tool Use Pattern)

使用 LLM 原生的函数调用能力（`save_memory` 工具），让模型主动决定如何整合记忆。

### 3. 观察者模式的变体

`MemoryStore` 被动响应 `AgentLoop` 的整合请求，通过 `Session` 对象传递状态。

---

## 依赖关系

```
memory.py
    │
    ├── 内部依赖
    │   ├── nanobot.utils.helpers.ensure_dir      # 目录创建工具
    │   ├── nanobot.providers.base.LLMProvider    # LLM 抽象接口
    │   └── nanobot.session.manager.Session       # 会话数据模型 (TYPE_CHECKING)
    │
    ├── 被依赖于
    │   ├── nanobot.agent.loop.AgentLoop._consolidate_memory()
    │   └── nanobot.agent.context.ContextBuilder.get_memory_context()
    │
    └── 外部库
        ├── pathlib.Path    # 文件路径操作
        ├── json            # JSON 序列化
        ├── typing          # 类型提示
        └── loguru.logger   # 日志记录
```

**注意：** `Session` 和 `LLMProvider` 使用 `TYPE_CHECKING` 延迟导入，避免循环依赖。

---

## 潜在改进建议

### 1. 记忆容量限制

当前 HISTORY.md 无大小限制，长期运行可能导致文件过大：

```python
# 建议：添加历史文件轮转
def append_history(self, entry: str, max_size_mb: int = 10) -> None:
    if self.history_file.stat().st_size > max_size_mb * 1024 * 1024:
        self._rotate_history()
    # ... existing logic
```

### 2. 整合失败重试机制

当前整合失败直接返回 `False`，可添加指数退避重试：

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def consolidate(self, ...):
    ...
```

### 3. 记忆版本控制

为 MEMORY.md 添加版本历史，支持回滚：

```python
def write_long_term(self, content: str) -> None:
    # 备份旧版本
    if self.memory_file.exists():
        backup = self.memory_dir / f"MEMORY.{datetime.now():%Y%m%d%H%M%S}.bak"
        shutil.copy(self.memory_file, backup)
    self.memory_file.write_text(content, encoding="utf-8")
```

### 4. 并发整合优化

多个会话可并行整合（当前已支持），但可考虑批量整合减少 LLM 调用：

```python
# 收集多个会话的待整合消息，一次 LLM 调用处理
async def batch_consolidate(self, sessions: list[Session], ...):
    ...
```

### 5. 记忆检索增强

当前只支持全量读取 MEMORY.md，可添加语义检索：

```python
def search_memory(self, query: str, top_k: int = 5) -> list[str]:
    """基于嵌入向量的语义搜索"""
    ...
```

### 6. 整合质量监控

添加整合结果的质量评估指标：

```python
def _evaluate_consolidation(self, before: str, after: str) -> dict:
    return {
        "compression_ratio": len(before) / len(after),
        "key_facts_preserved": self._count_facts(after),
        "timestamp": datetime.now().isoformat(),
    }
```

---

## 总结

Nanobot 的记忆系统是一个**轻量但完整的持久化方案**，通过：

1. **双层存储** 分离结构化知识与历史日志
2. **LLM 智能整合** 实现语义级别的记忆提炼
3. **异步非阻塞设计** 保证用户体验
4. **Append-Only 消息** 优化 LLM 缓存效率

该设计在简洁性与功能性之间取得了良好平衡，适合作为轻量级 AI Agent 的记忆基础设施。
