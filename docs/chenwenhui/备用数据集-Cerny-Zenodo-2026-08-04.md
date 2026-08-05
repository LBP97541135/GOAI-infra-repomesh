# 备用数据集：Cerny Zenodo 数据集方案

> 状态：**备选保留**，暂不采用，留作后续交叉验证参考
> 创建时间：2026-08-04

## 1. 数据集来源

| 字段 | 内容 |
|------|------|
| 论文 | Automated Change Impact Analysis for Microservices: A Static Analysis Approach |
| 作者 | Tomas Cerny 等（亚利桑那州立大学 + 挪威 Stavanger 大学 + 捷克 Brno 理工大学） |
| arXiv | arXiv:2501.11778（2025 年 1 月） |
| 数据地址 | https://zenodo.org/records/13922262 |
| 下载大小 | 50.4 MB（OutputValidationAnonymous.zip） |

## 2. 数据集结构

```
OutputValidation/
├── train-ticket/               ← P1，最完整
│   ├── IR/                     ← 323 个 JSON（每个 commit 一个，AST 中间表示）
│   ├── Delta/                  ← 322 个 JSON（相邻 commit 间的文件级差异）
│   ├── 25-validation-*.xlsx    ← 25 个手工标注的真实 breaking changes
│   ├── output-*.xlsx           ← 工具自动检测结果（323 commit × 4 规则）
│   └── validation-*.xlsx       ← 双盲人工验证结果（Rev1 vs Rev3）
├── java-microservice/          ← P2
├── Springboot-Microservice/    ← P3
├── spring-cloud-movie-*/       ← P4
├── sample-spring-*/            ← P5
├── spring-boot-*/              ← P6
├── microservices-basics-*/     ← P7
└── spring-microservices/       ← P8
```

## 3. 核心内容说明

### IR 文件（Intermediate Representation）

- 每个 commit 一个 JSON，约 3.4 MB
- 包含全部微服务的 AST 解析结果
- 字段：controllers / services / repositories / entities / feignClients / files
- 方法级信息：参数类型、返回类型、注解、包名
- **feignClients 字段全部为空**（Train-Ticket 不使用 @FeignClient）

### Delta 文件

- 记录相邻 commit 之间的文件级变更路径
- 字段：oldCommit / newCommit / changes[{oldPath, newPath, changeType, data}]
- data 字段通常为空（没有方法级 diff）

### 25 个验证案例

6 个 commit，共 25 个变更点，4 种规则分布：

| 规则 | 含义 | 示例 |
|------|------|------|
| IC（Invalid Call） | 调用方的方法签名已失效 | ts-preserve-service 调用 addAssuranceForOrder |
| UE（Uncalled Endpoint） | 端点不再被任何服务调用 | getAllConfigs、queryAll |
| SMM（Service Method Modified） | 服务层方法签名被修改 | calculateRefund、dispatchSeat |
| RMM（Repository Method Modified） | 仓储层方法签名被修改 | findById、findByOrderId |

结果：25/25 全部检出。

## 4. 与 RepoMesh 的差异分析

| 维度 | Cerny 方法 | RepoMesh 方法 |
|------|-----------|--------------|
| 输入 | 代码 diff（commit 级精确 AST） | 自然语言需求（一句话） |
| 分析对象 | 方法签名、参数类型、调用链 | 仓库级元数据（AutoCard） |
| 输出 | 受影响的组件/方法 | 受影响的仓库列表 |
| 调用关系来源 | AST 解析（但 feignClients 为空） | 静态提取 RestTemplate 调用（call_graph.json，90 条边） |
| 适用场景 | 精确的代码级变更影响分析 | 早期的需求级仓库发现 |

## 5. 结论：为什么暂不采用

1. **输入粒度不同**：Cerny 输入是代码 diff，我们是自然语言需求，无法直接复用其 Ground Truth
2. **调用图不完整**：IR 中 feignClients 全部为空，对我们无直接价值（我们自己的 call_graph.json 反而更完整）
3. **Ground Truth 视角不同**：Cerny 标注的是"全局代码中哪些 API 调用关系断了"，我们需要的是"一次变更波及哪些仓库"

## 6. 后续可借鉴的点

如果后续需要交叉验证，以下内容有参考价值：

- **IR 方法签名数据**：可用来验证我们 AutoCard 提取的 exposed_apis 是否准确
- **其他 7 个项目（P2~P8）**：可扩展验证集到非 Train-Ticket 项目
- **AST 解析思路**：Cerny 用 Java AST 精确解析，我们用 LLM 从 AutoCard 推断，两条路线可做对比
