# AgentLoop 代码分析文档

## 概述

`loop.py` 是 nanobot 的**核心处理引擎**，实现了完整的 Agent 消息处理循环。负责消息接收、上下文构建、LLM 调用、工具执行和响应发送。

---

## 类结构

### AgentLoop

```
AgentLoop
├── 核心组件
│   ├── bus: MessageBus          # 消息队列
│   ├── provider: LLMProvider    # LLM 提供者
│   ├── tools: ToolRegistry      # 工具注册表
│   ├── sessions: SessionManager # 会话管理
│   ├── context: ContextBuilder  # 上下文构建器
│   └── subagents: SubagentManager # 子代理管理
│
├── 配置参数
│   ├── workspace: Path          # 工作目录
│   ├── model: str               # 模型名称
│   ├── max_iterations: int      # 最大迭代次数 (默认40)
│   ├── temperature: float       # 温度 (默认0.1)
│   ├── max_tokens: int          # 最大token数 (默认4096)
│   └── memory_window: int       # 记忆窗口 (默认100)
│
└── 状态管理
    ├── _running: bool           # 运行状态
    ├── _mcp_connected: bool     # MCP连接状态
    ├── _active_tasks: dict      # 活跃任务映射
    ├── _consolidating: set      # 正在整合的会话
    └── _processing_lock: Lock   # 处理锁
```

---

## 核心流程

### 1. 主循环 (`run`)

```
┌─────────────────────────────────────────────────────────────┐
│                        run() 主循环                          │
├─────────────────────────────────────────────────────────────┤
│  1. _running = True                                         │
│  2. _connect_mcp() 连接MCP服务器                             │
│  3. while _running:                                         │
│     ├── wait_for(bus.consume_inbound(), timeout=1s)         │
│     ├── 超时 → continue                                     │
│     ├── /stop → _handle_stop()                              │
│     └── 普通消息 → create_task(_dispatch(msg))              │
└─────────────────────────────────────────────────────────────┘
```

### 2. 消息处理 (`_process_message`)

```
┌─────────────────────────────────────────────────────────────┐
│                   _process_message() 流程                    │
├─────────────────────────────────────────────────────────────┤
│  1. 系统消息处理 (channel == "system")                       │
│     └── 解析 channel:chat_id 格式                           │
│                                                             │
│  2. 斜杠命令处理                                             │
│     ├── /new  → 整合记忆 → 清空会话                          │
│     └── /help → 返回帮助信息                                 │
│                                                             │
│  3. 自动记忆整合触发                                         │
│     └── unconsolidated >= memory_window → 后台整合           │
│                                                             │
│  4. 构建上下文 & 运行代理循环                                 │
│     ├── _set_tool_context()                                 │
│     ├── context.build_messages()                            │
│     ├── _run_agent_loop()                                   │
│     └── _save_turn()                                        │
└─────────────────────────────────────────────────────────────┘
```

### 3. 代理迭代循环 (`_run_agent_loop`)

```
┌─────────────────────────────────────────────────────────────┐
│                  _run_agent_loop() 迭代                      │
├─────────────────────────────────────────────────────────────┤
│  while iteration < max_iterations:                          │
│  │                                                          │
│  ├── provider.chat() 调用LLM                                │
│  │                                                          │
│  ├── 有工具调用 (has_tool_calls):                            │
│  │   ├── on_progress() 发送思考内容                          │
│  │   ├── add_assistant_message() 添加助手消息                │
│  │   ├── tools.execute() 执行工具                            │
│  │   └── add_tool_result() 添加工具结果                      │
│  │                                                          │
│  └── 无工具调用:                                             │
│      ├── error → 记录错误，返回错误消息                       │
│      └── 正常 → 返回最终内容                                 │
│                                                             │
│  达到最大迭代 → 返回超限提示                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 工具系统

### 默认工具集

| 工具类 | 名称 | 功能 |
|--------|------|------|
| `ReadFileTool` | read_file | 读取文件 |
| `WriteFileTool` | write_file | 写入文件 |
| `EditFileTool` | edit_file | 编辑文件 |
| `ListDirTool` | list_dir | 列出目录 |
| `ExecTool` | exec | 执行Shell命令 |
| `WebSearchTool` | web_search | 网页搜索 |
| `WebFetchTool` | web_fetch | 获取网页内容 |
| `MessageTool` | message | 发送消息 |
| `SpawnTool` | spawn | 生成子代理 |
| `CronTool` | cron | 定时任务 |

### 工具上下文注入

```python
def _set_tool_context(channel, chat_id, message_id):
    # 为 message, spawn, cron 工具设置路由上下文
    for name in ("message", "spawn", "cron"):
        tool.set_context(channel, chat_id, ...)
```

---

## 并发控制机制

### 锁与状态

| 机制 | 类型 | 用途 |
|------|------|------|
| `_processing_lock` | `asyncio.Lock` | 全局消息处理互斥 |
| `_consolidation_locks` | `WeakValueDictionary[str, Lock]` | 每会话整合锁 |
| `_consolidating` | `set[str]` | 正在整合的会话标记 |
| `_active_tasks` | `dict[str, list[Task]]` | 活跃任务跟踪 |
| `_consolidation_tasks` | `set[Task]` | 整合任务强引用 |

### /stop 命令处理

```python
async def _handle_stop(msg):
    # 1. 取消所有活跃任务
    tasks = _active_tasks.pop(session_key, [])
    cancelled = sum(t.cancel() for t in tasks if not t.done())
    
    # 2. 取消子代理
    sub_cancelled = await subagents.cancel_by_session(session_key)
    
    # 3. 返回停止结果
    return f"⏹ Stopped {total} task(s)."
```

---

## 会话管理

### 会话生命周期

```
创建 → 消息处理 → 记忆整合 → 清空(/new)
  │                  │
  │                  └── unconsolidated >= memory_window
  │                      └── 后台异步整合
  │
  └── session.messages 持续增长
```

### 记忆整合触发条件

```python
unconsolidated = len(session.messages) - session.last_consolidated
if unconsolidated >= memory_window and session.key not in _consolidating:
    # 触发后台整合任务
    asyncio.create_task(_consolidate_and_unlock())
```

---

## 消息保存 (`_save_turn`)

### 处理规则

1. **空助手消息**: 跳过（防止上下文污染）
2. **工具结果**: 截断超过500字符的内容
3. **用户消息**: 
   - 移除运行时上下文前缀
   - Base64图片替换为 `[image]` 占位符
4. **时间戳**: 自动添加 ISO 格式时间戳

---

## MCP 集成

### 懒加载连接

```python
async def _connect_mcp():
    if _mcp_connected or _mcp_connecting or not _mcp_servers:
        return
    
    _mcp_connecting = True
    _mcp_stack = AsyncExitStack()
    await connect_mcp_servers(_mcp_servers, tools, _mcp_stack)
    _mcp_connected = True
```

### 连接管理

- **一次性连接**: 首次需要时懒加载
- **失败重试**: 下次消息时重试连接
- **优雅关闭**: `close_mcp()` 清理资源

---

## 辅助方法

### `_strip_think`

移除某些模型返回的 `<think>...</think>` 思考块。

```python
re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
```

### `_tool_hint`

格式化工具调用为简洁提示：

```python
# 输入: tool_call(name="web_search", arguments={"query": "Python tutorial"})
# 输出: '