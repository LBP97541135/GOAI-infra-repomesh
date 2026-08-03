# CLI 注册体验优化 — 从必填多参数到只输入 source

**日期**: 2026-08-03
**作者**: chenwenhui

---

## 一、改了什么

`register` 命令从需要手动填 `--name` + `--url` + `--path` 三个必填参数，简化为接受**一个或多个**位置参数 `sources`（URL 或本地路径），其余全部自动推断。注册表条数完全由用户输入的 source 数量决定。

### 之前

```bash
python scripts/repomesh_cli.py register \
    --name order-service \
    --url https://github.com/org/order-service \
    --path D:\repos\order-service
```

### 现在

```bash
# 注册一个
python scripts/repomesh_cli.py register D:\repos\order-service

# 注册多个——空格分隔，混合 URL 和路径都行
python scripts/repomesh_cli.py register \
    D:\repos\order-service \
    D:\repos\payment-service \
    https://github.com/org/frontend-web

# 本地路径自动 scan，URL 只推断名字
# 可选覆盖（仅单个 source 时生效）
python scripts/repomesh_cli.py register D:\repos\order-service \
    --name order-service --description "订单服务"
```

---

## 二、设计推理过程

### 第一性原理

仓库发现的核心是让 LLM 看到足够的仓库信息。信息来源有三个：

| 来源 | 谁出力 | 什么信息 |
|------|--------|---------|
| A. git clone + scan | 系统自动 | top_dirs / deps / recent_commits / exposed_apis / low_signal |
| B. 远程仓库 API | 系统自动 | name / description / topics / languages |
| C. 人工填 | 用户 | name / url / description / topics / languages |

### 逐字段对抗性审查

**url**：必须人工输入。这是唯一的真正前提——系统需要知道仓库在哪。但 url 不总是最佳入口，用户手里可能已经有本地 clone。

**name**：不需要人填。从 url 末段或 path 末段自动提取。

**description**：不需要人填。GitHub API 返回。即使 API 不通，AutoCard（deps + dirs + commits + apis）比一句 description 信息量大得多。

**topics / languages**：不需要人填。GitHub API 返回 topics。languages 从依赖文件推断更准（有 `go.mod` 就是 Go，有 `package.json` 就是 Node）。

### 对抗性审查：自动方案会失败的场景

| 场景 | 问题 | 解法 |
|------|------|------|
| 私有仓库，系统没有 clone 权限 | scan 跑不了 | 用户提供本地路径 |
| 仓库还没 push 到远程 | 没有 url | 用户直接给本地路径 |
| 仓库没写 description | API 返回空 | AutoCard 补上 |
| 仓库是 monorepo 子目录 | clone 整个 monorepo 太重 | 用户给本地路径 |
| 仓库数量很多 | 每次都 clone 太慢 | 预扫描 + 缓存（V1） |

### 结论

**用户最少只需输入一个东西：仓库 url 或本地路径。** 其他全部自动。

---

## 三、新增的辅助函数

### `infer_name(source: str) -> str`

从 URL 或本地路径推断仓库名：

```python
infer_name("https://github.com/org/order-service")   → "order-service"
infer_name("git@github.com:org/payment.git")         → "payment"
infer_name("D:\\repos\\user-service")                  → "user-service"
infer_name("./components/agentteams/hermes")         → "hermes"
```

逻辑：strip 尾部 `/` 和 `.git`，取最后一个路径分隔符（`/`、`\`、`:`）后的段。

### `infer_languages(repo_path: Path | str) -> tuple[str, ...]`

从依赖文件存在性推断编程语言：

| 文件 | 语言 |
|------|------|
| requirements.txt / pyproject.toml / setup.py | python |
| package.json | javascript |
| go.mod | go |
| Cargo.toml | rust |
| pom.xml / build.gradle / build.gradle.kts | java |
| composer.json | php |
| Gemfile | ruby |

### `resolve_source(source: str) -> dict`

CLI 内部的统一入口函数。接收 url 或 path，返回 `{name, url, path, auto_card, languages}`：
- 本地目录存在 → scan + infer_languages + infer_name
- 否则按 URL 处理 → infer_name，无 AutoCard

---

## 四、改动文件清单

| 文件 | 改动 |
|------|------|
| `src/.../application/scan.py` | 新增 `infer_name()`、`infer_languages()`、`_LANGUAGE_FILES` 常量 |
| `src/.../application/__init__.py` | 导出 `infer_name`、`infer_languages` |
| `scripts/repomesh_cli.py` | 新增 `resolve_source()`；`cmd_register` 改为位置参数 `sources`（`nargs="+"`，支持多个），`--name`/`--url` 变可选（仅单 source 时生效）；`cmd_register_bulk` 支持 `source` 字段；argparse 更新；文档字符串更新 |
| `scripts/run_mvp_validation.ps1` | 配置区简化为 `source` 列表 |
| `tests/test_domain.py` | 新增 7 个测试：infer_name（https / ssh / path / trailing slash）、infer_languages（python / multiple / empty） |

---

## 五、register-bulk JSON 格式变化

### 之前

```json
[
    {"name": "order-service", "url": "...", "path": "/local/path"},
    {"name": "frontend", "url": "...", "description": "...", "topics": ["react"]}
]
```

### 现在

```json
[
    {"source": "https://github.com/org/order-service"},
    {"source": "D:\\repos\\payment-service", "description": "Payment processing"},
    {"source": "D:\\repos\\frontend-web", "topics": ["react", "typescript"]}
]
```

每个 entry 只需要 `source`，其余全部自动推断。老格式（`url` + `path`）仍然兼容。

---

## 六、测试结果

```
29 passed in 1.03s
```

新增 7 个测试全部通过：
- `test_infer_name_from_https_url`
- `test_infer_name_from_ssh_url`
- `test_infer_name_from_local_path`
- `test_infer_name_strips_trailing_slash`
- `test_infer_languages_python`
- `test_infer_languages_multiple`
- `test_infer_languages_empty`

ruff: `All checks passed!`
