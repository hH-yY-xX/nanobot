# nanobot CLI 模块

本文档列出了 `commands.py` 中定义的所有方法及其说明。

## 内部辅助方法

| 方法名 | 说明 |
|-------|------|
| `_flush_pending_tty_input()` | 丢弃模型生成输出期间输入的未读按键 |
| `_restore_terminal()` | 将终端恢复到原始状态（回显、行缓冲等） |
| `_init_prompt_session()` | 创建带有持久化文件历史的 prompt_toolkit 会话 |
| `_print_agent_response(response, render_markdown)` | 使用一致的终端样式渲染助手响应 |
| `_is_exit_command(command)` | 当输入应该结束交互式聊天时返回 True |
| `_read_interactive_input_async()` | 使用 prompt_toolkit 读取用户输入（处理粘贴、历史、显示） |
| `version_callback(value)` | 版本信息回调函数 |
| `main(version)` | nanobot - 个人 AI 助手（主回调） |

## 核心命令

| 方法名 | 说明 |
|-------|------|
| `onboard()` | 初始化 nanobot 配置和工作空间 |
| `_make_provider(config)` | 根据配置创建适当的 LLM 提供商 |

## 网关命令

| 方法名 | 说明 |
|-------|------|
| `gateway(port, verbose)` | 启动 nanobot 网关 |
| `on_cron_job(job)` | 通过 agent 执行 cron 任务 |
| `_pick_heartbeat_target()` | 为心跳触发的消息选择一个可路由的频道/聊天目标 |
| `on_heartbeat_execute(tasks)` | 第二阶段：通过完整的 agent 循环执行心跳任务 |
| `on_heartbeat_notify(response)` | 将心跳响应传递到用户的频道 |

## Agent 命令

| 方法名 | 说明 |
|-------|------|
| `agent(message, session_id, markdown, logs)` | 直接与 agent 交互 |
| `_thinking_ctx()` | 当日志关闭时显示旋转器；当日志开启时跳过 |
| `_cli_progress(content, tool_hint)` | CLI 进度回调函数 |
| `run_once()` | 单消息模式 —— 直接调用，不需要总线 |
| `run_interactive()` | 交互模式 —— 像其他频道一样通过总线路由 |

## 频道命令

| 方法名 | 说明 |
|-------|------|
| `channels_status()` | 显示频道状态 |
| `_get_bridge_dir()` | 获取桥接目录，如果需要则进行设置 |
| `channels_login()` | 通过二维码链接设备 |

## Cron 命令

| 方法名 | 说明 |
|-------|------|
| `cron_list(all)` | 列出定时任务 |
| `cron_add(name, message, every, cron_expr, tz, at, deliver, to, channel)` | 添加定时任务 |
| `cron_remove(job_id)` | 删除定时任务 |
| `cron_enable(job_id, disable)` | 启用或禁用任务 |
| `cron_run(job_id, force)` | 手动运行任务 |
| `on_job(job)` | Cron 任务执行回调 |

## 状态命令

| 方法名 | 说明 |
|-------|------|
| `status()` | 显示 nanobot 状态 |

## OAuth 登录命令

| 方法名 | 说明 |
|-------|------|
| `provider_login(provider)` | 使用 OAuth 提供商进行身份验证 |
| `_register_login(name)` | 注册登录处理器的装饰器 |
| `_login_openai_codex()` | OpenAI Codex OAuth 登录 |
| `_login_github_copilot()` | GitHub Copilot OAuth 登录 |
