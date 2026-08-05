# RepoMesh 数据集

> 创建时间：2026-08-04
> 用途：RepoMesh 仓库发现（repository discovery）能力的验证集

## 1. 文件夹结构

```
datasets/
├── README.md                    ← 本说明文件
├── train-ticket/                ← 现采用数据集（主验证集）
│   ├── validation_cases.json    ← 15 个测试用例（需求 → 标准答案）
│   ├── call_graph.json          ← Train-Ticket 的静态调用图（90 条边）
│   └── scoring.py               ← 评分脚本（Recall / Precision / F1）
└── cerny-backup/                ← 备用数据集说明
    └── README.md                ← Cerny Zenodo 备用数据集说明
```

## 2. 现采用数据集：Train-Ticket

`train-ticket/` 是当前唯一启用的验证集，基于开源微服务基准项目
**Train-Ticket**（`https://github.com/FudanSELab/train-ticket`）构建。

验证目标：给定一句**业务语言**的自然语言需求，RepoMesh 能否找出需要修改的仓库。

每个测试用例来自一次真实的 git commit，按下面三层标注 Ground Truth：

| 层次 | 含义 | Recall 权重 |
|------|------|------------|
| `direct` | commit 实际修改了文件的仓库（来自 `git diff --name-only`） | 0.6 |
| `propagated` | 调用链上受影响、但未被直接修改的仓库（来自 `call_graph.json`） | 0.2 |
| `context` | 同业务域、可能需要配合修改的仓库（人工判断，允许遗漏） | 0.2 |

设计依据与全部 15 个用例的逆推过程见
`docs/chenwenhui/验证集方案-TrainTicket-2026-08-04.md`。

### 用例分布

| 维度 | 分布 |
|------|------|
| 难度 | easy 9 个 / medium 4 个 / hard 2 个 |
| 对抗性 | none 7 个 / cross-domain 4 个 / trap 3 个 / synonym 1 个 |

### 快速使用

```bash
# 1. 评分脚本自检
python datasets/train-ticket/scoring.py --self-test

# 2. 评分一次 RepoMesh 运行（单文件，case_id → 仓库列表）
python datasets/train-ticket/scoring.py \
    --predictions run.json \
    --report report.txt \
    --summary summary.json

# 3. 评分一个运行目录（每个用例一个 <case_id>.json）
python datasets/train-ticket/scoring.py \
    --predictions-dir runs/2026-08-04/ \
    --report report.txt
```

评分公式：

```
recall    = 0.6 * direct_recall + 0.2 * propagated_recall + 0.2 * context_recall
precision = |predicted ∩ (direct ∪ propagated ∪ context)| / |predicted|
f1        = 2 * precision * recall / (precision + recall)
```

## 3. 备用数据集：Cerny Zenodo

`cerny-backup/` **暂不启用**，仅保留作为后续交叉验证的参考来源。

之所以当前不采用，是因为 Cerny 数据集的输入是**代码 diff（commit 级 AST）**，
而 RepoMesh 的输入是**自然语言需求**，两者的 Ground Truth 视角无法直接对齐；
另外 Cerny 的 IR 中 `feignClients` 字段全部为空，对我们没有直接价值（我们自己
从 RestTemplate 调用提取的 `call_graph.json` 反而更完整）。

详细分析与后续可借鉴的点见
`docs/chenwenhui/备用数据集-Cerny-Zenodo-2026-08-04.md`，
以及 `cerny-backup/README.md`。

## 4. 与代码仓库的关系

本目录是**评测数据资产**，不属于 `repomesh` Python 包：

- `scoring.py` 是独立脚本，仅依赖 Python 标准库（`json` / `argparse` / `pathlib`），
  不导入 `repomesh.*`，因此可以单独复制到任意机器上运行。
- RepoMesh 业务模块不会读取本目录；本目录的消费者是 CI / 离线评测流程。
- 修改验证集用例时，请同步更新 `docs/chenwenhui/验证集方案-TrainTicket-2026-08-04.md`
  的逆推记录，保持证据可追溯。
