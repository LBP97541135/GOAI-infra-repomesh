# Cerny Zenodo 备用数据集

> 状态：**备选保留**，暂不采用，留作后续交叉验证参考
> 创建时间：2026-08-04

本目录**只存放说明文档**，不包含 Cerny 数据集本体（数据需从 Zenodo 按需下载）。
完整分析见 `docs/chenwenhui/备用数据集-Cerny-Zenodo-2026-08-04.md`。

## 1. 来源

| 字段 | 内容 |
|------|------|
| 论文 | Automated Change Impact Analysis for Microservices: A Static Analysis Approach |
| 作者 | Tomas Cerny 等（亚利桑那州立大学 + 挪威 Stavanger 大学 + 捷克 Brno 理工大学） |
| arXiv | arXiv:2501.11778（2025 年 1 月） |
| 数据地址 | https://zenodo.org/records/13922262 |
| 下载大小 | 50.4 MB（`OutputValidationAnonymous.zip`） |

数据集结构（解压后 `OutputValidation/`）：

```
├── train-ticket/               ← P1，最完整（IR/Delta/25 个标注案例）
├── java-microservice/          ← P2
├── Springboot-Microservice/    ← P3
├── spring-cloud-movie-*/       ← P4
├── sample-spring-*/            ← P5
├── spring-boot-*/              ← P6
├── microservices-basics-*/     ← P7
└── spring-microservices/       ← P8
```

其中 P1（train-ticket）最完整，包含 323 个 commit 的 AST 中间表示（IR）、
322 个相邻 commit 间的文件级差异（Delta），以及 25 个手工标注的真实
breaking changes，覆盖 4 种规则（IC / UE / SMM / RMM）。

## 2. 为什么暂不采用

1. **输入粒度不同**：Cerny 的输入是**代码 diff（commit 级精确 AST）**，
   RepoMesh 的输入是**自然语言需求**，无法直接复用其 Ground Truth。
2. **调用图不完整**：Cerny 的 IR 中 `feignClients` 字段全部为空
   （Train-Ticket 不使用 `@FeignClient`），对我们无直接价值——我们自己
   从 RestTemplate 调用提取的 `../train-ticket/call_graph.json` 反而更完整。
3. **Ground Truth 视角不同**：Cerny 标注的是"全局代码中哪些 API 调用关系
   断了"，RepoMesh 需要的是"一次变更波及哪些仓库"。

## 3. 后续可借鉴的点

如果后续需要交叉验证，以下内容有参考价值：

- **IR 方法签名数据**：可用来验证 AutoCard 提取的 `exposed_apis` 是否准确。
- **其他 7 个项目（P2~P8）**：可把验证集扩展到非 Train-Ticket 项目。
- **AST 解析思路**：Cerny 用 Java AST 精确解析，RepoMesh 用 LLM 从 AutoCard
  推断，两条路线可做对比研究。

## 4. 启用方式（如果将来要采用）

1. 从 `https://zenodo.org/records/13922262` 下载 `OutputValidationAnonymous.zip`。
2. 解压后将需要的项目子目录（如 `train-ticket/IR`、`train-ticket/Delta`）
   放到本目录下，并在本 README 中记录数据快照的版本与下载日期。
3. 在 `docs/chenwenhui/` 下新增一份"启用方案"，说明如何把 Cerny 的
   breaking-change 标注映射成 RepoMesh 的 `direct / propagated / context`
   三层 Ground Truth，并补充对应的 `scoring.py` 适配逻辑。
