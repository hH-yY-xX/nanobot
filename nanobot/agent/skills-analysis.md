# Skills 技能加载器代码分析

## 概述

该模块实现了 Agent 的**技能加载系统**，负责发现、加载和管理 Agent 可用的技能。技能以 Markdown 文件 (`SKILL.md`) 形式存在，用于教会 Agent 如何使用特定工具或执行特定任务。

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                     SkillsLoader                            │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐        ┌─────────────────────────┐    │
│  │ workspace/skills│        │ builtin_skills (内置)   │    │
│  │   (工作区技能)   │        │ nanobot/skills/         │    │
│  │   优先级: 高     │        │ 优先级: 低              │    │
│  └────────┬────────┘        └───────────┬─────────────┘    │
│           │                             │                   │
│           └──────────┬──────────────────┘                   │
│                      ▼                                      │
│              ┌───────────────┐                              │
│              │  技能合并列表  │                              │
│              │ (去重, 过滤)   │                              │
│              └───────────────┘                              │
└─────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. 常量定义

| 常量 | 值 | 说明 |
|------|-----|------|
| `BUILTIN_SKILLS_DIR` | `nanobot/skills/` | 内置技能目录路径 |

### 2. SkillsLoader 类

#### 构造函数

```python
SkillsLoader(workspace: Path, builtin_skills_dir: Path | None = None)
```

| 参数 | 说明 |
|------|------|
| `workspace` | 工作区根目录 |
| `builtin_skills_dir` | 内置技能目录（可选，默认使用 `BUILTIN_SKILLS_DIR`） |

#### 核心方法

| 方法 | 功能 | 返回值 |
|------|------|--------|
| `list_skills()` | 列出所有可用技能 | `list[dict]` |
| `load_skill(name)` | 按名称加载单个技能内容 | `str \| None` |
| `load_skills_for_context()` | 加载多个技能用于上下文注入 | `str` |
| `build_skills_summary()` | 构建 XML 格式的技能摘要 | `str` |
| `get_always_skills()` | 获取标记为始终启用的技能 | `list[str]` |
| `get_skill_metadata()` | 获取技能的 frontmatter 元数据 | `dict \| None` |

## 技能发现流程

```
┌──────────────────┐
│   list_skills()  │
└────────┬─────────┘
         │
    ┌────▼────┐
    │ 扫描工作 │
    │ 区技能   │
    └────┬────┘
         │
    ┌────▼────┐
    │ 扫描内置 │
    │ 技能     │
    └────┬────┘
         │
    ┌────▼────────┐
    │ 去重        │
    │ (工作区优先) │
    └────┬────────┘
         │
    ┌────▼─────────────┐
    │ filter_unavailable│
    │ = True?          │
    └────┬─────────────┘
    Yes /    \ No
       /      \
┌─────▼─────┐  ┌▼──────────┐
│ 检查依赖   │  │ 返回全部  │
│ 过滤不可用 │  │           │
└─────┬─────┘  └───────────┘
      │
      ▼
   返回结果
```

## 技能元数据结构

### SKILL.md Frontmatter 格式

```yaml
---
description: "技能描述"
always: true
metadata: '{"nanobot": {"requires": {"bins": ["git"], "env": ["GITHUB_TOKEN"]}}}'
---
```

### 元数据字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `description` | string | 技能简短描述 |
| `always` | boolean | 是否始终加载到上下文 |
| `metadata` | JSON string | nanobot 特定配置 |

### nanobot 元数据结构

```json
{
  "nanobot": {
    "always": true,
    "requires": {
      "bins": ["git", "gh"],
      "env": ["GITHUB_TOKEN"]
    }
  }
}
```

## 依赖检查机制

```
┌─────────────────────────────┐
│    _check_requirements()    │
└──────────────┬──────────────┘
               │
       ┌───────▼───────┐
       │ 获取 requires │
       │ 配置          │
       └───────┬───────┘
               │
    ┌──────────▼──────────┐
    │  检查 bins 列表     │
    │  (shutil.which)     │
    └──────────┬──────────┘
               │
         缺失? ─┬─ Yes ──► 返回 False
               │
    ┌──────────▼──────────┐
    │  检查 env 列表      │
    │  (os.environ.get)   │
    └──────────┬──────────┘
               │
         缺失? ─┬─ Yes ──► 返回 False
               │
               ▼
          返回 True
```

## 技能优先级

```
┌─────────────────────────────────────────────┐
│              技能加载优先级                  │
├─────────────────────────────────────────────┤
│  1. workspace/skills/{name}/SKILL.md (最高) │
│  2. builtin_skills/{name}/SKILL.md   (次之) │
└─────────────────────────────────────────────┘
```

- **工作区技能**优先于**内置技能**
- 同名技能只加载工作区版本（覆盖内置）

## 输出格式

### build_skills_summary() XML 输出

```xml
<skills>
  <skill available="true">
    <name>github</name>
    <description>GitHub CLI 操作技能</description>
    <location>/path/to/skills/github/SKILL.md</location>
  </skill>
  <skill available="false">
    <name>tmux</name>
    <description>Tmux 会话管理</description>
    <location>/path/to/skills/tmux/SKILL.md</location>
    <requires>CLI: tmux</requires>
  </skill>
</skills>
```

### load_skills_for_context() 输出

```markdown
### Skill: github

[技能内容，去除 frontmatter]

---

### Skill: memory

[技能内容，去除 frontmatter]
```

## 辅助方法

| 方法 | 功能 |
|------|------|
| `_strip_frontmatter()` | 移除 Markdown 的 YAML frontmatter |
| `_parse_nanobot_metadata()` | 解析 JSON 格式的 nanobot 元数据 |
| `_get_skill_meta()` | 获取技能的 nanobot 配置 |
| `_get_skill_description()` | 获取技能描述（fallback 到技能名） |
| `_get_missing_requirements()` | 获取缺失依赖的描述信息 |

## 设计模式

1. **策略模式**: 通过 `filter_unavailable` 参数控制过滤策略
2. **模板方法**: 统一的技能加载流程，可配置的依赖检查
3. **优先级覆盖**: 工作区技能覆盖内置技能的机制

## 兼容性处理

```python
# 支持 nanobot 和 openclaw 两种元数据 key
data.get("nanobot", data.get("openclaw", {}))
```

## 依赖关系

| 依赖 | 用途 |
|------|------|
| `json` | 解析元数据 JSON |
| `os` | 环境变量检查 |
| `re` | frontmatter 正则匹配 |
| `shutil` | CLI 工具检查 (`which`) |
| `pathlib.Path` | 路径操作 |

## 潜在改进建议

1. **缓存机制**: 添加技能列表和元数据缓存，避免重复文件 I/O
2. **异步加载**: 支持异步技能加载，提升性能
3. **版本管理**: 添加技能版本号支持
4. **热重载**: 支持运行时技能更新检测
5. **依赖解析**: 支持技能间的依赖声明
6. **YAML 解析**: 使用专业 YAML 库替代简单的字符串分割解析
