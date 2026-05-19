# MiniAgent

一个轻量级 AI Agent 编排框架，支持**按需加载领域知识（Skills）**、工具调用（Tool Use）与交互式命令行界面。

## 架构概览

```
用户输入 → Agent Loop → Anthropic API (DeepSeek) → 模型选择工具调用
                                                          ↓
                                            ┌──── 工具执行 ────┐
                                            │ bash (命令执行)     │
                                            │ read_file (读文件)  │
                                            │ write_file (写文件) │
                                            │ edit_file (编辑文件) │
                                            │ load_skill (加载知识) │
                                            └──────────────────┘
                                                          ↓
                                                    返回结果 → 继续循环
```

## 快速开始

### 1. 安装依赖

```bash
pip install anthropic pyyaml
```

### 2. 设置 API Key

```bash
export MINI_API_KEY="your-api-key-here"
```

默认使用 DeepSeek 兼容 Anthropic 格式的 API 端点，可在 `agent.py` 中修改 `BASE_URL` 和 `MODEL` 变量以切换其他模型提供商。

### 3. 运行

```bash
python3 agent.py
```

启动后进入交互式命令行界面，输入 `q` 或 `exit` 退出。

## 核心概念

### Agent Loop

`agent_loop()` 是代理的核心循环：

1. 将用户消息 + 历史记录发送给 LLM
2. 若 LLM 返回 `tool_use`（工具调用请求），执行对应工具并将结果返回给 LLM
3. 重复步骤 1–2，直到 LLM 返回最终文本回复（`stop_reason != "tool_use"`）

### 工具系统

| 工具 | 功能 | 说明 |
|------|------|------|
| `bash` | 执行 shell 命令 | 支持基本命令，含黑名单安全检查 |
| `read_file` | 读取文件内容 | 含路径穿越防护 |
| `write_file` | 写入/创建文件 | 自动创建父目录 |
| `edit_file` | 精确文本替换编辑 | 单次替换，需精确匹配 |
| `load_skill` | 加载领域知识 | 按需从 `skills/` 目录加载 |

### Skill 系统

Skill 是领域知识的载体，存放在 `skills/<name>/SKILL.md` 文件中，采用 **YAML frontmatter + Markdown 正文** 格式：

```markdown
---
name: skill-name
description: Short description shown in system prompt
---

# Skill Body

Full knowledge content loaded on demand via `load_skill` tool.
```

**工作流程**：
- **Layer 1（系统提示）**：启动时扫描所有 SKILL.md，将名称和描述注入 system prompt，让模型知晓可用技能
- **Layer 2（按需加载）**：当模型调用 `load_skill` 时，返回完整的 skill 正文内容

#### 内置 Skills

| Skill | 描述 |
|-------|------|
| `code-review` | 代码安全审查，涵盖安全、正确性、性能、可维护性检查 |
| `pdf` | PDF 文件处理（读取、创建、合并、拆分） |

## 项目结构

```
miniagent/
├── agent.py              # 主代理入口：工具定义、Agent Loop、CLI
├── secure.md             # 代码安全审查报告（agent.py 的审计文档）
├── README.md             # 本文档
└── skills/
    ├── code-review/
    │   └── SKILL.md      # 代码审查技能
    └── pdf/
        └── SKILL.md      # PDF 处理技能
```

## 关键实现细节

### SkillLoader

`SkillLoader` 类负责扫描和加载技能文件：
- 递归搜索所有 `SKILL.md` 文件
- 解析 YAML frontmatter（`---` 分隔的元数据）
- 提供 `get_descriptions()`（用于 system prompt）和 `get_content(name)`（按需加载）两个接口

### 安全机制

- **路径防护**：`safe_path()` 使用 `resolve()` + `is_relative_to()` 防止路径穿越
- **命令黑名单**：`run_bash()` 内置基础黑名单屏蔽危险命令
- > ⚠️ 注意：当前安全机制为轻量级防护，生产环境建议加强白名单或沙箱机制

## 自定义扩展

### 添加新 Skill

1. 在 `skills/` 下创建目录 `skills/my-skill/SKILL.md`
2. 编写带有 YAML frontmatter 的 Markdown 文件：

```markdown
---
name: my-skill
description: What this skill does
---

# My Skill

Detailed instructions and knowledge here.
```

3. 重启 agent，新技能自动生效

### 添加新工具

在 `agent.py` 中：

1. 实现处理函数（如 `run_my_tool`）
2. 注册到 `TOOL_HANDLERS` 字典
3. 在 `TOOLS` 列表中添加工具 schema 定义

## 环境要求

- Python 3.9+（依赖 `pathlib.Path.is_relative_to()`）
- 依赖库：`anthropic`、`pyyaml`

## 许可证

MIT
