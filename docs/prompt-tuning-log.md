# Prompt 调优记录

> 创建时间：2026-08-05
> 模块：仓库发现（Repository Discovery）+ 仓库确认（Repository Confirmation）
> 模型：DeepSeek-V3

---

## 一、当前生产环境 Prompt（V6）

### 1.1 总 Manager — 发现阶段

**文件**：`src/repomesh/modules/repository_intelligence/application/discovery.py`
**函数**：`_build_discovery_prompt()`

#### System Prompt

```
Select repositories that may require code changes. Return only a JSON array
of objects with repository, confidence, and rationale fields. Prefer recall.
```

#### User Prompt

```json
{
  "requirement": "<需求文本>",
  "repositories": [
    {"name": "ts-seat-service", "signals": "<searchable_text>"},
    {"name": "ts-preserve-service", "signals": "<searchable_text>"},
    ...
  ]
}
```

#### 特点

- 极简 prompt，只有一句话
- 指令 "Prefer recall" 导致 LLM 倾向多报
- `signals` 来自 `profile.searchable_text`（包含 name + deps + commits + apis）

#### V6 实测效果

| 指标 | 值 |
|------|-----|
| Recall | **84.6%** |
| Precision | 50.3% |
| 平均候选数 | 10.2 |

> ⚠️ 注意：P1 prompt 收紧（"POSITIVE EVIDENCE"、"will need changes"）的改动在 git 操作中丢失了。当前用的是更早的版本。

---

### 1.2 Team Manager — 确认阶段

**文件**：`src/repomesh/modules/repository_intelligence/application/confirmation.py`
**函数**：`_build_confirmation_prompt()`

#### System Prompt

```
You are the Repository Manager for a specific repository.
Given your repository's details and a feature requirement, you must
decide whether YOUR repository actually needs code changes.

IMPORTANT RULES:
- The Project Manager has already identified your repository as a
  candidate, which means there is initial evidence of relevance.
- Default to REQUIRED or MAYBE unless you have CLEAR evidence that
  your repository is NOT affected by this requirement.
- Use EXCLUDED only when your repository handles a completely
  different concern than what the requirement describes.

STATUS DEFINITIONS:
- REQUIRED: Your repository has APIs, dependencies, or code that
  directly corresponds to the requirement.
- MAYBE: Your repository might be indirectly affected (e.g. depends
  on a service that will change) but you are not certain.
- EXCLUDED: Your repository is clearly unrelated to the requirement.

Return ONLY a JSON object (no markdown fences, no extra text):
{
  "status": "REQUIRED" or "MAYBE" or "EXCLUDED",
  "confidence": 0.0 to 1.0,
  "reason": "one sentence explanation citing specific evidence",
  "plan_summary": "if REQUIRED or MAYBE, brief description of the change",
  "missing_dependencies": ["repos you depend on that are NOT in the candidate list"]
}
```

#### User Prompt

```
## Your Repository: ts-preserve-service

Top directories: src, src/main, src/test
Dependencies: ts-common, spring-boot-starter-amqp, jakarta.validation-api
Recent commits:
  - Hotfix (#243)
  - Reconstruction (#227)
  - fix: send email exception
Exposed APIs: (none)

## Requirement

修复票价计算和座位分配相关的 bug。...

## All Candidates Flagged by Discovery

ts-seat-service, ts-price-service, ts-preserve-service, ts-preserve-other-service

## Project Manager's Assessment of Your Repository

The Project Manager flagged your repository with confidence 0.90:
"handles ticket reservation and has email-related commits"

Please verify whether this assessment is correct. If you cannot
find evidence to contradict it, lean towards REQUIRED or MAYBE.

## Task

Does YOUR repository (ts-preserve-service) need code changes for this
requirement? Return the JSON object now.
```

#### V6 实测效果

| 指标 | 发现阶段 | 确认后 | 变化 |
|------|---------|--------|------|
| Recall | 84.6% | 83.5% | -1.1% |
| Precision | 50.3% | 50.7% | +0.3% |
| 排除率 | — | 2.0% | — |

**问题**：排除率只有 2%，确认阶段几乎不过滤。

---

## 二、Prompt 历史版本

### 2.1 发现阶段 Prompt 演进

#### V1 原始版（已被覆盖）

```
You are a repository discovery assistant for a large engineering organisation.
Given a natural-language feature requirement and a catalog of repositories,
you must identify which repositories are likely to need changes.

Return ONLY a JSON array. Each element must have exactly these keys:
  - "repository": the repository name (must match the catalog)
  - "confidence": float between 0 and 1
  - "rationale": one-sentence explanation
```

**低信号建议（V1）**：
```
⚠️ The following repositories have insufficient information (low_signal).
You may not be able to determine whether they are relevant.
If unsure, include them as candidates with confidence ≤ 0.5
and explain the uncertainty in the rationale: ts-xxx, ts-yyy
```

**问题**："likely" + "if unsure, include" 鼓励多报。

#### V2 P1 收紧版（已丢失）

```
You are a repository discovery assistant for a large engineering organisation.
Given a natural-language feature requirement and a catalog of repositories,
you must identify which repositories will need changes.

IMPORTANT RULES:
- Only include repositories where you have POSITIVE EVIDENCE
  (shared dependencies, API calls, or commit history) linking them
  to the requirement.
- Do NOT include repositories merely because they are in the same domain.
- For each repository you include, the confidence should reflect
  how certain you are that changes are needed, not how related the
  topic seems.
```

**低信号建议（V2）**：
```
⚠️ The following repositories have insufficient information (low_signal):
ts-xxx, ts-yyy
Do NOT include these unless you have concrete evidence linking
them to the requirement.
```

**效果**：几乎无效（LLM 仍给大多数仓库 ≥0.7 分）。

#### V6 当前版

```
Select repositories that may require code changes. Return only a JSON array
of objects with repository, confidence, and rationale fields. Prefer recall.
```

**效果**：Recall 84.6%，Precision 50.3%。

---

### 2.2 确认阶段 Prompt 演进

#### V3 严格版（矫枉过正）

```
You are the Repository Manager for a specific repository.
Given your repository's details and a feature requirement, you must
decide whether YOUR repository actually needs code changes.

IMPORTANT RULES:
- Be strict: only say REQUIRED if you have concrete evidence from
  your repo's files, dependencies, APIs, or commit history.
- Do NOT say REQUIRED just because the topic seems related or
  because other repos in the same domain are affected.
- If your repository handles a different concern than what the
  requirement describes, say EXCLUDED.
```

**效果**：排除率 75%，Recall 暴跌至 50.9%，Precision 飙升至 93.3%。

#### V4/V6 当前版（默认保留）

```
IMPORTANT RULES:
- The Project Manager has already identified your repository as a
  candidate, which means there is initial evidence of relevance.
- Default to REQUIRED or MAYBE unless you have CLEAR evidence that
  your repository is NOT affected by this requirement.
- Use EXCLUDED only when your repository handles a completely
  different concern than what the requirement describes.
```

**效果**：排除率 2%，Recall 83.5%，Precision 50.7%。

---

## 三、问题诊断

### 确认阶段排除率的历史变化

```
V3（严格）:     75%  ← 太严格，Recall 暴跌
V4（默认保留）:  1%   ← 太宽松，不起作用
V6（同 V4）:    2%   ← 同上

目标:           20~30% ← 中间点
```

### V3 vs V4/V6 prompt 差异核心

| 维度 | V3 严格 | V4/V6 宽松 |
|------|---------|-----------|
| 默认判断 | 不确定 → EXCLUDED | 不确定 → REQUIRED/MAYBE |
| REQUIRED 门槛 | 需要 concrete evidence | 有 initial evidence 即可 |
| EXCLUDED 门槛 | 不同 concern 即排除 | 需 CLEAR evidence 才排除 |

**核心矛盾**：V3 和 V4 都走了极端。V3 要求 Manager 找到证据才保留，V4 要求 Manager 找到证据才排除。两者都没有找到正确的平衡。

---

## 四、V7 调优结果

### Prompt 改动

两个 prompt 全部改为中文，并做了以下调优：

**总 Manager（发现阶段）**：
```
你是一个大型工程组织的仓库发现助手。
给定一个自然语言需求和一个仓库目录，你需要找出哪些仓库需要修改代码。

核心原则：保证召回率（Recall）优先，宁多勿漏。

判断依据（按优先级）：
1. 仓库的依赖（deps）和需求有直接对应关系
2. 仓库暴露的 API 和需求中提到的功能相关
3. 最近的 commit 和需求主题相关
4. 仓库名称和需求关键词有语义匹配
```

**Team Manager（确认阶段）**——从 V4 的"默认保留"改为"基于证据判断"：
```
## 判断流程
第 1 步：看你的仓库的依赖（deps）和 API 是否和需求有直接对应关系。
第 2 步：看你的仓库的 commit 历史是否和需求主题相关。
第 3 步：如果以上都没有关联，判断你的仓库功能是否和需求完全无关。

## 判断标准
- 仅共享 ts-common 等公共依赖不能作为 REQUIRED 的依据
- 如果你的 deps 中有和需求直接对应的依赖，倾向于 REQUIRED
- 如果你的仓库和需求描述的功能属于不同业务域，倾向于 EXCLUDED
- 如果不确定，选 MAYBE 而不是直接 EXCLUDED
```

关键变化：
- 去掉了 "Default to REQUIRED"（太宽松）
- 去掉了 "If you cannot find evidence to contradict it, lean towards REQUIRED"（偏向太强）
- 改为中立表述："请验证这个判断是否正确"
- 新增具体判断标准（ts-common 不算、不同业务域倾向 EXCLUDED）

### V7 验证集结果

#### 最终指标

| 指标 | V4（英文宽松） | V6（英文+pom.xml修复） | V7（中文+调优） |
|------|--------------|---------------------|---------------|
| Recall | 78.3% | 90.8% | 73.8% |
| Precision | 80.4% | 55.7% | **70.6%** |
| F1 | **76.4%** | 65.9% | 69.7% |

#### 分阶段指标（核心！）

| 指标 | 发现阶段（总 Manager） | 确认后（Team Manager） | 变化 |
|------|----------------------|----------------------|------|
| Recall | **80.2%** | 63.7% | -16.5% |
| Precision | 54.5% | **69.0%** | +14.6% |
| F1 | 64.9% | 66.3% | +1.4% |
| 排除率 | — | **37.3%** | — |

### 排除率历史对比

```
V3（严格英文）:    75%  ← 太严格，Recall 暴跌
V4（宽松英文）:     1%  ← 太宽松，不起作用
V6（宽松+pom.xml）:  2%  ← 同上
V7（调优中文）:    37%  ← 终于在合理区间！
```

**V7 的确认阶段终于发挥了过滤作用**——排除率 37.3%，Precision 从 54.5% 提升到 69.0%（+14.6%）。

### 问题：Recall 损失过大

V7 的确认阶段排除率 37% 偏高，导致 Recall 从 80.2% 跌到 63.7%（-16.5%）。具体看哪些仓库被误排：

| 用例 | 发现阶段找到 | 确认后误排 | 误排的 GT 仓库 |
|------|------------|----------|--------------|
| TT-009 | 15 个 | 8 个 | food, admin-basic, basic, order, seat 等 5 个 GT |
| TT-007 | 8 个 | 2 个 | preserve-other, security |
| TT-015 | 8 个 | 6 个 | preserve, cancel |

Team Manager 排除太激进了，把一些真正需要的仓库也排了。

---

## 五、下一版调优方向（V8）

### 目标

```
排除率：20~25%（V7 的 37% 偏高）
Recall 损失：-5% 以内（V7 损失 16.5% 太多）
Precision 提升：+10% 以上（V7 提升 14.6%）
```

### V7 → V8 修改方向

| V7 问题 | V8 修改 |
|---------|---------|
| "不同业务域倾向 EXCLUDED" 太激进 | 改为"完全不同的业务域才 EXCLUDED" |
| 排除率 37% 偏高 | 降低 EXCLUDED 的倾向，更多用 MAYBE |
| Recall 损失 16.5% | 增加提示："如果不确定，选 MAYBE 而不是 EXCLUDED" 已有但需要强调 |
