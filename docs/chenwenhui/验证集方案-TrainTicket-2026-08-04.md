# Train-Ticket 验证集方案设计

> 创建时间：2026-08-04
> 状态：方案设计阶段，待确认后实施

---

## 一、第一性原理：我们在验证什么

### 1.1 核心问题

RepoMesh 的核心能力是：**给定一句自然语言需求，找出哪些仓库需要被修改。**

验证集要回答的问题不是"LLM 能不能做对"，而是：

> **"一个人看了这个需求，能不能判断出该改哪些仓库？"**

如果人都判断不出来，就不能要求机器判断出来。所以验证集的核心是构造一组 **"需求 → 标准答案"** 对，其中标准答案是从 git 历史中逆向提取的客观事实。

### 1.2 需求描述应该多精准

这是最关键的设计决策。先定义三个粒度层级：

| 层级 | 示例 | 特点 |
|------|------|------|
| **模糊需求** | "优化订票流程" | 太模糊，几乎可以命中任何服务，无法验证 |
| **业务需求** | "修复订票流程中发送通知邮件的异常" | 描述了业务功能和行为，不涉及任何技术细节 |
| **技术需求** | "修改 ts-preserve-service 的 sendEmail 方法调用" | 暴露了服务名、方法名，等于直接给了答案 |

**结论：采用"业务需求"层级。**

理由（从第一性原理推导）：

1. **用户不会说技术细节。** 真实场景中，产品经理或技术 leader 说的是"支付回调老是超时，查一下"，而不是"修改 ts-payment-service 的 PaymentServiceImpl 第 47 行"。所以验证集的输入必须是业务语言。

2. **但不能太模糊。** "优化系统性能"这种话，连开发者都不知道改哪里，不能用来验证。需求必须包含足够的信息让一个熟悉系统的人能定位到功能模块。

3. **业务需求对应的就是 commit message 的"人类可读版本"。** 我们从 commit message 逆推时，要把 "fix: send email exception" 改写成 "修复订票和取消订单流程中发送通知邮件的异常"，而不是 "注释掉 PreserveServiceImpl 的 sendEmail 调用"。

### 1.3 Ground Truth 怎么构建

一个 commit 的 Ground Truth 不是简单地说"改了哪几个仓库就标哪几个"。要分三个层次：

```
Ground Truth = 直接命中 ∪ 传播命中 ∪ 上下文命中
```

| 层次 | 含义 | 来源 | 在 Recall/Precision 中的角色 |
|------|------|------|----------------------------|
| **直接命中** | commit 实际修改了文件的仓库 | `git diff --name-only` | 必须命中，漏了就是 Recall 下降 |
| **传播命中** | 没有被改，但因为调用链关系，逻辑上也会受影响 | `call_graph.json` 的下游 | 应该命中，但容忍少量遗漏 |
| **上下文命中** | 和这次变更属于同一业务域，可能需要配合修改 | 人工判断 | 允许遗漏，不参与 Precision 惩罚 |

**为什么需要"传播命中"这一层？**

举一个真实例子：commit `5b9edca5` 注释掉了三个服务中的 `sendEmail()` 调用。这三个服务（ts-preserve-service、ts-preserve-other-service、ts-cancel-service）是直接命中。但 `ts-notification-service` 是被调用的下游——它提供了 sendEmail 对应的邮件发送接口。如果需求是"把邮件通知改为异步消息队列"，那 ts-notification-service 也需要改（从同步 HTTP 接口改成消息消费者）。所以它是传播命中。

**为什么需要"上下文命中"这一层？**

因为真实开发中，改了一个地方后，开发者可能会顺手检查同一业务域的其他服务。比如改了 ts-order-service 的订单查询，开发者可能会检查 ts-order-other-service（其他订单）是否也需要同步改。这种判断是主观的，不应该用来惩罚系统。

### 1.4 对抗性审查：这套方案会被怎么质疑

**质疑 1："你的 Ground Truth 是从 commit 逆推的，但 commit 本身可能改多了或改少了。"**

这是事实。有些 commit 改了不该改的文件（比如 IDE 自动格式化），有些 commit 漏改了该改的文件（后来另一个 commit 补上）。我们无法保证每个 commit 的 diff 都是完美的标准答案。

**应对**：这就是我们做三层 Ground Truth 的原因。"直接命中"来自 git diff 是客观事实，但"传播命中"和"上下文命中"给出了容错空间。在评分时：
- 直接命中没找全 → Recall 下降（严重）
- 传播命中没找全 → Recall 轻微下降（可容忍）
- 多报了不在任何一层的仓库 → Precision 下降（需要人工复核是否误报）

**质疑 2："你的需求描述是你自己编的，可能恰好编成了 LLM 容易猜对的样子。"**

这是验证集设计中最危险的偏差（confirmation bias）。如果我写"修复订票中发送邮件的异常"，LLM 看到"订票"就猜 ts-preserve-service，看到"邮件"就猜 ts-notification-service，这不代表它真的理解了系统，可能只是在匹配关键词。

**应对**：设计需求描述时，刻意加入三类对抗性测试：
- **同义替换测试**：同一组 Ground Truth，用不同的措辞描述（如"通知"vs"邮件"vs"消息推送"），验证系统是否依赖关键词匹配
- **陷阱测试**：需求中提到一个服务名但实际不需要改它（如"优化用户登录的安全检查"看起来要改 ts-login-service，但实际 Ground Truth 是 ts-security-service）
- **跨域传播测试**：需求只涉及一个功能点，但 Ground Truth 包含调用链上的远端服务（如"修改票价计算逻辑"需要改 ts-price-service，但传播到 ts-preserve-service 和 ts-seat-service）

**质疑 3："train-ticket 是一个理想化的微服务 demo，真实仓库的结构远没有这么清晰。"**

完全正确。train-ticket 的服务划分很干净，服务名就是功能名（ts-order-service 就是订单服务），这让 LLM 很容易猜对。真实仓库不会有这么好的命名。

**应对**：train-ticket 验证集的定位是 **"基础能力测试"**，不是"生产环境验证"。它的作用是验证系统的基本逻辑是否正确（能不能找到直接相关的仓库 + 能不能沿着调用链传播），而不是验证在混乱代码库上的鲁棒性。后者需要用真实仓库测试。

**质疑 4："你的 call_graph.json 只提取了 RestTemplate 调用，会漏掉很多隐式依赖。"**

确实如此。我们的 call_graph.json 是通过正则匹配 `getServiceUrl("ts-xxx-service")` 提取的，只能发现显式的 HTTP 调用。以下依赖会被遗漏：
- 消息队列（Kafka/RabbitMQ 的 topic 订阅关系）
- 共享数据库（多个服务访问同一个 DB）
- API Gateway 层的路由规则
- gRPC 调用

**应对**：在 train-ticket 的场景下，这个遗漏是可控的——因为 train-ticket 确实只使用 RestTemplate 做 HTTP 调用，没有消息队列和 gRPC。所以 call_graph.json 的覆盖率对这个验证集来说是足够的。但在真实场景中，AutoCard 里的 `deps` 和 `exposed_apis` 会补充这些信号。

---

## 二、验证集结构设计

### 2.1 数据格式

每个测试用例是一个 JSON 对象：

```json
{
  "id": "TT-001",
  "source_commit": "5b9edca54250e1e5e82ebe38e9245efd97e8d474",
  "requirement": "修复订票和取消订单流程中发送通知邮件的异常",
  "entry_repo": "ts-preserve-service",
  "ground_truth": {
    "direct": ["ts-preserve-service", "ts-preserve-other-service", "ts-cancel-service"],
    "propagated": ["ts-notification-service"],
    "context": []
  },
  "commit_message": "fix: send email exception",
  "difficulty": "easy",
  "anti_bias_type": "none"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | TT-001 ~ TT-N |
| source_commit | string | 对应的 git commit hash |
| requirement | string | 自然语言需求描述（业务语言） |
| entry_repo | string | 入口仓库（场景 1 用，表示用户给的 URL 对应的仓库） |
| ground_truth.direct | string[] | commit 直接改了文件的仓库（必须命中） |
| ground_truth.propagated | string[] | 调用链上受影响但未直接修改的仓库（应该命中） |
| ground_truth.context | string[] | 同业务域可能需要配合修改的仓库（允许遗漏） |
| commit_message | string | 原始 commit message（参考用） |
| difficulty | string | easy / medium / hard |
| anti_bias_type | string | none / synonym / trap / cross-domain（对抗性测试类型） |

### 2.2 评分公式

```python
# direct 命中率（权重 0.6）
direct_recall = len(predicted ∩ direct) / len(direct)

# propagated 命中率（权重 0.2）
propagated_recall = len(predicted ∩ propagated) / len(propagated) if propagated else 1.0

# 综合 Recall
recall = 0.6 * direct_recall + 0.2 * propagated_recall + 0.2 * context_recall

# Precision：报出来的仓库中，有多少在任意一层
all_truth = direct ∪ propagated ∪ context
precision = len(predicted ∩ all_truth) / len(predicted)

# F1
f1 = 2 * precision * recall / (precision + recall)
```

### 2.3 难度分级标准

| 难度 | 标准 | 预期 |
|------|------|------|
| easy | direct 仓库 ≤ 3 个，服务名和需求关键词高度相关 | Recall ≥ 90%，Precision ≥ 70% |
| medium | direct 仓库 4~8 个，或需要理解调用链才能找到 propagated | Recall ≥ 80%，Precision ≥ 60% |
| hard | direct 仓库 ≥ 9 个，或包含跨域传播/陷阱测试 | Recall ≥ 70%，Precision ≥ 50% |

---

## 三、第一档测试用例（7 个，message 清晰）

这些 commit 的 message 本身接近业务语言，几乎可以直接作为需求输入。

### TT-001: fix send email exception

| 字段 | 值 |
|------|------|
| commit | `5b9edca5` |
| requirement | **修复订票和取消订单流程中发送通知邮件的异常** |
| entry_repo | ts-preserve-service |
| direct | ts-preserve-service, ts-preserve-other-service, ts-cancel-service |
| propagated | ts-notification-service（三个服务都调用了它的邮件接口） |
| context | [] |
| difficulty | easy |
| anti_bias_type | none |

**逆推过程**：diff 显示三个服务的 `sendEmail(notifyInfo, headers)` 被注释掉，加了 TODO "change to async message service"。说明邮件发送功能出了问题。三个服务是直接改的，ts-notification-service 是邮件发送的下游提供者。

### TT-002: fix price bug

| 字段 | 值 |
|------|------|
| commit | `1c932a7f` |
| requirement | **修复票价计算和座位分配相关的 bug** |
| entry_repo | ts-seat-service |
| direct | ts-seat-service, ts-preserve-service, ts-preserve-other-service, ts-order-other-service, ts-route-plan-service, ts-travel-service, ts-travel2-service, ts-security-service, ts-auth-service |
| propagated | ts-price-service（票价服务的下游）、ts-config-service（座位服务读取配置）、ts-order-service（票价影响订单） |
| context | [] |
| difficulty | hard |
| anti_bias_type | cross-domain（需求只提"票价"，但 Ground Truth 涉及座位、安全、认证等远端服务） |

**逆推过程**：diff 主要是清理 ts-seat-service 中的废弃代码注释、添加 @Slf4j 日志。但同时改了 9 个服务的 entity 和 service，说明是一次跨服务的接口调整。

### TT-003: fix admin api bug

| 字段 | 值 |
|------|------|
| commit | `2ef30fcf` |
| requirement | **修复管理后台 API 的数据模型不一致问题** |
| entry_repo | ts-admin-basic-info-service |
| direct | ts-admin-basic-info-service, ts-admin-order-service, ts-admin-route-service, ts-admin-travel-service, ts-admin-user-service, ts-contacts-service, ts-order-other-service, ts-order-service, ts-user-service |
| propagated | [] |
| context | [] |
| difficulty | medium |
| anti_bias_type | none |

**逆推过程**：diff 显示所有 admin-*-service 的 entity 类被大幅精简（移除重复字段），同时调整了下游的 contacts/order/user 服务以适配新模型。

### TT-004: Hotfix #243

| 字段 | 值 |
|------|------|
| commit | `313886e9` |
| requirement | **修复订单管理、用户管理和寄存服务的线上 hotfix** |
| entry_repo | ts-admin-order-service |
| direct | ts-admin-order-service, ts-admin-user-service, ts-consign-price-service, ts-consign-service, ts-order-other-service, ts-order-service, ts-preserve-service, ts-rebook-service, ts-station-service, ts-user-service |
| propagated | [] |
| context | ts-cancel-service（取消订单流程也涉及订单状态变更） |
| difficulty | hard |
| anti_bias_type | none |

### TT-005: fix route bug

| 字段 | 值 |
|------|------|
| commit | `bf5555ed` |
| requirement | **修复路线查询和站点数据传递的 bug** |
| entry_repo | ts-route-plan-service |
| direct | ts-admin-basic-info-service, ts-admin-order-service, ts-basic-service, ts-order-service, ts-route-plan-service, ts-travel2-service, ts-travel-plan-service |
| propagated | ts-route-service（路线数据的提供者）、ts-station-service（站点查询的提供者） |
| context | [] |
| difficulty | medium |
| anti_bias_type | cross-domain（需求提"路线"，但传播到了站点和基础服务） |

### TT-006: Fix bug in queryStationId

| 字段 | 值 |
|------|------|
| commit | `a20d1612` |
| requirement | **新增车站 ID 的批量查询功能** |
| entry_repo | ts-station-service |
| direct | ts-station-service, ts-order-service, ts-order-other-service, ts-travel-service |
| propagated | ts-basic-service（调用 station 的下游）、ts-preserve-service（通过 basic 间接调用） |
| context | [] |
| difficulty | easy |
| anti_bias_type | trap（需求说"新增功能"看起来只改 station，但实际还需要 order 来调用批量接口） |

**逆推过程**：diff 显示 station-service 新增了 QueryByIdBatch/QueryForIdBatch 类和对应方法，order-service 和 order-other-service 新增了对应的 Controller 端点和 Service 方法，travel-service 改了一行调用。

### TT-007: fix ui api bug

| 字段 | 值 |
|------|------|
| commit | `45d84b44` |
| requirement | **修复前端调用订票和支付相关 API 的 bug** |
| entry_repo | ts-preserve-other-service |
| direct | ts-food-service, ts-order-service, ts-preserve-other-service, ts-seat-service, ts-security-service |
| propagated | [] |
| context | [] |
| difficulty | medium |
| anti_bias_type | synonym（需求说"订票"对应 preserve，说"支付"但 Ground Truth 里没有 payment-service，而是 security-service，这是一个关键词陷阱） |

---

## 四、第二档测试用例（8 个，message 模糊但 diff 可推）

这些 commit 的 message 是 "modify code details" 或 "fix bug" 这种无信息量的描述，需要从 diff 内容逆推业务含义。

### TT-008: delete sso login register

| 字段 | 值 |
|------|------|
| commit | `a6c5f71f` |
| requirement | **删除独立的 SSO 登录注册模块，用统一的认证服务替代** |
| entry_repo | ts-auth-service |
| direct | ts-login-service, ts-register-service, ts-sso-service, ts-security-service |
| propagated | [] |
| context | ts-user-service（用户数据可能需要迁移到 auth 体系） |
| difficulty | easy |
| anti_bias_type | none |

**逆推过程**：diff 显示 ts-login-service、ts-register-service、ts-sso-service 的全部 Java 文件被删除（-2581 行），ts-security-service 的 initData 被修改（引用了新的认证方式）。

### TT-009: modify config + station service

| 字段 | 值 |
|------|------|
| commit | `4df9a5e2` |
| requirement | **重构配置管理和车站信息服务的接口实现** |
| entry_repo | ts-config-service |
| direct | ts-config-service, ts-station-service, ts-food-service |
| propagated | ts-admin-basic-info-service（管理端调用 config）、ts-seat-service（座位服务读取 config）、ts-basic-service（基础服务调用 station）、ts-order-service（订单服务调用 station） |
| context | [] |
| difficulty | medium |
| anti_bias_type | cross-domain（需求只提"配置"和"车站"，但传播到了座位和管理服务） |

### TT-010: payment + preserve code details

| 字段 | 值 |
|------|------|
| commit | `48e8f8e9` |
| requirement | **调整支付流程和订票保存服务的代码细节** |
| entry_repo | ts-payment-service |
| direct | ts-payment-service, ts-preserve-service, ts-preserve-other-service |
| propagated | ts-order-service（支付完成后通知订单）、ts-inside-payment-service（内部支付） |
| context | [] |
| difficulty | easy |
| anti_bias_type | none |

### TT-011: notification + order code details

| 字段 | 值 |
|------|------|
| commit | `2459960e` |
| requirement | **调整通知服务和订单管理的数据处理逻辑** |
| entry_repo | ts-notification-service |
| direct | ts-notification-service, ts-order-other-service, ts-order-service |
| propagated | ts-cancel-service（取消订单时发通知）、ts-preserve-service（订票成功后发通知） |
| context | [] |
| difficulty | easy |
| anti_bias_type | none |

### TT-012: fix modify user bug

| 字段 | 值 |
|------|------|
| commit | `228066f5` |
| requirement | **修复用户信息修改功能的 bug** |
| entry_repo | ts-user-service |
| direct | ts-auth-service, ts-user-service |
| propagated | ts-admin-user-service（管理端可能需要同步修改） |
| context | [] |
| difficulty | easy |
| anti_bias_type | trap（需求只提"用户"，但需要改 auth-service 做认证适配） |

### TT-013: fix bug in Order class

| 字段 | 值 |
|------|------|
| commit | `32686beb` |
| requirement | **修复订单数据模型的 bug** |
| entry_repo | ts-order-service |
| direct | ts-order-service, ts-order-other-service |
| propagated | ts-preserve-service（创建订单时引用 Order）、ts-cancel-service（取消订单时引用 Order）、ts-rebook-service（改签时引用 Order） |
| context | [] |
| difficulty | easy |
| anti_bias_type | cross-domain（需求只提"订单"，但传播到了订票、取消、改签） |

### TT-014: fix bug in ticker-info-service

| 字段 | 值 |
|------|------|
| commit | `3addb104` |
| requirement | **修复车次信息查询服务的 bug** |
| entry_repo | ts-ticketinfo-service |
| direct | ts-ticketinfo-service, ts-travel-service |
| propagated | ts-basic-service（基础服务聚合车次信息） |
| context | [] |
| difficulty | easy |
| anti_bias_type | none |

### TT-015: repair ts-notification-service

| 字段 | 值 |
|------|------|
| commit | `122fb40b` |
| requirement | **修复通知服务的接口调用问题** |
| entry_repo | ts-notification-service |
| direct | ts-preserve-service, ts-preserve-other-service |
| propagated | ts-cancel-service（也调用通知服务） |
| context | [] |
| difficulty | easy |
| anti_bias_type | trap（entry_repo 是 notification，但直接改的是 preserve 服务） |

---

## 五、对抗性测试设计总结

| 类型 | 用例 | 目的 |
|------|------|------|
| **synonym（同义替换）** | TT-007 | 需求说"支付"但答案里没 payment-service，验证是否只是关键词匹配 |
| **trap（陷阱）** | TT-006, TT-012, TT-015 | 入口仓库/需求提到的服务 ≠ 实际要改的仓库 |
| **cross-domain（跨域传播）** | TT-002, TT-005, TT-009, TT-013 | 需求只涉及一个功能点，但 Ground Truth 包含调用链远端 |
| **none（基准）** | TT-001, TT-003, TT-004, TT-008, TT-010, TT-011, TT-014 | 正常测试，无对抗性 |

---

## 六、实施计划

1. **当前阶段**：本方案确认后，生成正式的 `validation_cases.json` 文件
2. **执行阶段**：将 JSON 喂给 RepoMesh CLI，逐个跑 `repomesh run "<requirement>" <entry_repo_url>`
3. **评分阶段**：自动计算 Recall/Precision/F1，输出报告
4. **迭代阶段**：根据失败用例分析 LLM 的推理过程，优化 prompt 或 AutoCard 信息量

### 预期目标

| 指标 | easy 用例 | medium 用例 | hard 用例 | 整体 |
|------|----------|------------|----------|------|
| Recall | ≥ 90% | ≥ 80% | ≥ 70% | ≥ 80% |
| Precision | ≥ 70% | ≥ 60% | ≥ 50% | ≥ 60% |
