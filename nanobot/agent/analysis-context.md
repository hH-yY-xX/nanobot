# ContextBuilder 代码分析文档

## 概述

`context.py` 是 nanobot 的**上下文构建器**，负责组装 Agent 的提示词（Prompt），包括系统提示、历史消息、运行时上下文和多媒体内容。

---

## 类结构

### ContextBuilder

```
ContextBuilder
├── 依赖组件
│   ├── workspace: Path          # 工作目录
│   ├── memory: MemoryStore      # 记忆存储
│   └── skills: SkillsLoader     # 技能加载器
│
├── 类常量
│   ├── BOOTSTRAP_FILES          # 引导文件列表
│   └── _RUNTIME_CONTEXT_TAG     # 运行时上下文标记
│
└── 核心方法
    ├── build_system_prompt()    # 构建系统提示
    ├── build_messages()         # 构建完整消息列表
    ├── add_tool_result()        # 添加工具结果
    └── add_assistant_message()  # 添加助手消息
```

---

## 核心流程

### 1. 系统提示构建 (`build_system_prompt`)

```
┌─────────────────────────────────────────────────────────────┐
│              build_system_prompt() 构建流程                  │
├─────────────────────────────────────────────────────────────┤
│  1. _get_identity()                                         │
│     ├── 基础身份信息 (nanobot 🐈)                            │
│     ├── 运行时信息 (OS, Python版本)                          │
│     ├── 工作空间路径                                         │
│     └── 平台策略 (Windows/POSIX)                             │
│                                                             │
│  2. _load_bootstrap_files()                                  │
│     ├── AGENTS.md                                           │
│     ├── SOUL.md                                             │
│     ├── USER.md                                             │
│     └── TOOLS.md                                            │
│                                                             │
│  3. memory.get_memory_context()                              │
│     └── 长期记忆内容                                         │
│                                                             │
│  4. skills.get_always_skills()                               │
│     └── 始终激活的技能                                       │
│                                                             │
│  5. skills.build_skills_summary()                            │
│     └── 可用技能列表                                         │
│                                                             │
│  6. 用 "\n\n---\n\n" 连接所有部分                            │
└─────────────────────────────────────────────────────────────┘
```

### 2. 消息列表构建 (`build_messages`)

```
┌─────────────────────────────────────────────────────────────┐
│                build_messages() 构建流程                     │
├─────────────────────────────────────────────────────────────┤
│  输入: history, current_message, media, channel, chat_id    │
│                                                             │
│  1. _build_runtime_context()                                │
│     ├── 当前时间 (YYYY-MM-DD HH:MM)                         │
│     ├── 时区信息                                            │
│     ├── Channel 名称                                        │
│     └── Chat ID                                             │
│                                                             │
│  2. _build_user_content()                                   │
│     ├── 纯文本消息                                          │
│     └── 多媒体消息 (Base64编码图片)                          │
│                                                             │
│  3. 合并运行时上下文和用户内容                               │
│     └── 避免连续同角色消息 (某些提供商限制)                   │
│                                                             │
│  返回: [system, ...history, user]                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 引导文件系统

### BOOTSTRAP_FILES

| 文件名 | 用途 |
|--------|------|
| `AGENTS.md` | Agent 配置和角色定义 |
| `SOUL.md` | 核心个性和行为准则 |
| `USER.md` | 用户偏好和上下文 |
| `TOOLS.md` | 工具使用说明 |

### 加载逻辑

```python
def _load_bootstrap_files(self) -> str:
    for filename in self.BOOTSTRAP_FILES:
        file_path = self.workspace / filename
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            parts.append(f"## {filename}\n\n{content}")
```

---

## 平台策略

### Windows 策略

```markdown
## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
```

### POSIX 策略

```markdown
## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
```

---

## 多媒体处理

### 图片编码流程 (`_build_user_content`)

```
输入图片路径列表
    │
    ├── 检查文件是否存在
    ├── 读取二进制内容
    ├── detect_image_mime() 检测MIME类型
    ├── base64.b64encode() 编码
    └── 构建 image_url 对象

返回格式:
[
  {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
  {"type": "text", "text": "用户消息"}
]
```

### 支持的图片类型

通过 `detect_image_mime()` 从 magic bytes 检测，支持：
- PNG
- JPEG
- GIF
- WebP
- 等常见图片格式

---

## 运行时上下文

### 格式示例

```
[Runtime Context — metadata only, not instructions]
Current Time: 2026-03-10 14:30 (Tuesday) (CST)
Channel: telegram
Chat ID: 123456789
```

### 设计目的

- **非指令性**: 标记明确说明这只是元数据
- **动态信息**: 每次请求时更新时间和上下文
- **合并策略**: 与消息内容合并，避免连续 user 角色消息

---

## 消息操作方法

### add_tool_result

```python
def add_tool_result(messages, tool_call_id, tool_name, result):
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": result
    })
```

### add_assistant_message

```python
def add_assistant_message(messages, content, tool_calls=None, 
                          reasoning_content=None, thinking_blocks=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if reasoning_content:
        msg["reasoning_content"] = reasoning_content
    if thinking_blocks:
        msg["thinking_blocks"] = thinking_blocks
```

---

## 设计亮点

| 特性 | 说明 |
|------|------|
| **模块化构建** | 系统提示由多个独立部分组装而成 |
| **平台感知** | 自动检测 OS 并注入相应策略 |
| **多媒体支持** | 自动检测并编码图片为 Base64 |
| **运行时注入** | 动态时间/上下文信息，非硬编码 |
| **避免角色冲突** | 合并运行时上下文到用户消息，避免连续同角色 |
| **技能系统集成** | 自动加载 always-on 技能和技能摘要 |

---

## 与 AgentLoop 的关系

```
AgentLoop._process_message()
    │
    ├── context.build_messages()  ←── ContextBuilder
    │       ├── build_system_prompt()
    │       ├── _build_runtime_context()
    │       └── _build_user_content()
    │
    ├── provider.chat(messages)   ←── 使用构建的消息列表
    │
    ├── context.add_assistant_message()  ←── 添加助手响应
    │
    ├── context.add_tool_result()        ←── 添加工具结果
    │
    └── _save_turn()              ←── 保存到会话历史
```
