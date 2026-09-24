# ExPerT 与知返 Re: Coach：相似点及整合升级方向

> 分析对象：本仓库当前代码，以及 Park、Tark、Gong 的 [ExPerT（ACL 2026）](https://aclanthology.org/2026.acl-long.959/)；论文细节按用户提供的 `ExPerT.pdf` 核对。A、B 已进入实现；D 的对照评估工具已提供，实际收益仍待真实数据验证。

## 结论

最值得借鉴的是把**“用户这轮想要多深”**与**“用户对这轮概念实际熟悉到什么程度”**拆成两个信号。知返已有逐轮任务解析、局部深度、命题状态、作用域记忆和确定性上下文编译器，可以实现低成本、可被用户纠正的逐问教学起点，并通过真实对照评估效果。这遵守项目“少调用、低延迟、不宣称掌握度”的教学原则。

## 1. 论文与项目各自解决什么问题

ExPerT 把问题表示为“当前问题文本 + 输入过程的按键行为 → 推断该问题领域的熟练度 → 按熟练度生成回答”。其五级标签为 Novice、Beginner、Intermediate、Proficient、Expert；行为特征包括按键按下/抬起间隔、删除次数和输入速度，按词聚合后进入 few-shot 推断提示，再用熟练度条件控制细节、术语和概念复杂度（论文 §2.1–2.3，图 2–3）。它关注的是**同一个人跨领域、跨子问题会变化的即时熟练度**，而不是固定人设。

知返面向机器学习/深度学习教学，当前链路是“澄清问题 → 形成 `ResolvedTask` → 检索作用域记忆和命题状态 → 编译教学上下文 → 单次主 Coach 输出”。`gate.py` 从措辞得到 `desired_depth` 和任务类型；`compiler.py` 把局部深度、已知前置、记忆及命题状态放进提示。长期记忆主要保存讲解偏好和交互规则；`Concept State` 记录带证据来源的命题状态，不是领域能力评分。相关实现见 [`gate.py`](../recoach-server/app/services/gate.py)、[`compiler.py`](../recoach-server/app/services/compiler.py)、[`brief.py`](../recoach-server/app/services/brief.py)、[`orchestrator.py`](../recoach-server/app/services/orchestrator.py)。

## 2. 相似点与关键差异

| 角度 | 相似点 | 目前的差异与机会 |
|---|---|---|
| 个性化单位 | 两者都把当前问题作为调整回答的重要输入。 | ExPerT 显式预测逐问熟练度；知返现在有独立的 `TeachingStart`（`unknown / novice / familiar / advanced`），证据来自明确措辞、同概念反馈与同命题状态，只调讲解路径，不作能力判定。 |
| 回答策略 | 都会改变讲解细节和术语密度。 | 知返已有 L0–L5 局部深度及教学基线；论文的五级熟练度不能机械映射为 L0–L5，因为“想看推导”不等于“已具备推导前置”。 |
| 历史信息 | 两者都可利用同一用户的先前交互。 | ExPerT 的最佳推断设置使用同一用户的 10 个示例；知返保存显式偏好、会话 Brief 与命题状态，适合把这些作为有作用域的证据，而非生成固定人设。 |
| 信号来源 | 当前问题的语义都是重要线索。 | ExPerT 使用按键时间特征；本次知返升级使用明确措辞、命题状态和概念反馈。见 [`teaching.py`](../recoach-server/app/services/teaching.py)。 |
| 可解释性 | 都有让个性化结果可追溯的空间。 | 知返已有记忆选择 trace、Event Ledger 和前端个性化依据；`context_compiled` 事件已记录本轮 `teachingStart`，“本轮为何从某个起点讲”可追溯，但推断不等于用户已掌握的事实。 |

一个具体误判入口是 [`_detect_depth`](../recoach-server/app/services/gate.py)：当前“深入/详细/完整/彻底”会触发 L3，而 `auto` 在上下文编译时落为 L2。要求“详细讲”可能只是希望解释充分，并不说明能直接接受公式。论文表 2 也展示了：开放式提问可能来自专家，术语流利或打字流利也未必表示真正理解。**问题形式、回答偏好、已有知识需要分别处理。**

## 3. 值得整合的升级方向

### 方向 A：建立逐问、逐概念的“教学起点”判断（优先级最高）

在 `ResolvedTask` 形成后、`compile_context` 前，增加内部的 `ExpertiseContext`，建议至少包含 `level = unknown / novice / familiar / advanced`、`evidence`、`scope` 和 `source`。这里的 level 只用于选择前置补充和术语解释，不作为对用户能力的公开判定；初期无需复制论文的五级分类。`desired_depth` 保持为**本轮目标深度**，两者同时存在。例如用户第一次问“请详细推导反向传播”，可保持推导目标，但从链式法则与变量含义起步，不把他直接视作高级用户。

证据优先顺序建议为：本轮明确表述（如“我第一次学”“跳过基础”）→ 当前概念/命题的明确历史反馈 → 当前问题的弱语义线索 → `unknown`。`self_reported_understood` 只能支持对对应命题的局部起点调整，不能升级为整个领域“熟练”。信号冲突时先保留不确定性，必要时沿用现有澄清门控只问一个真正改变讲法的问题。用户的本轮明确要求始终可以覆盖推断。

落点：[`schemas.py`](../recoach-server/app/schemas.py) 定义内部结构；[`orchestrator.py`](../recoach-server/app/services/orchestrator.py) 组合证据；[`compiler.py`](../recoach-server/app/services/compiler.py) 编译“教学起点/术语解释/目标深度”三个独立提示字段。TUI 的独立流水线需同步同一规则，见 [`re-coach-tui/src/orchestrator.ts`](../re-coach-tui/src/orchestrator.ts)。

### 方向 B：用反馈校正“教学起点”，而不是固化人设

现有 `Concept State` 区分 `introduced`、`unresolved`、`self_reported_understood`、`corrected` 等状态，且有 `evidence_kind`；这比论文的单次五级自报更适合教学闭环。可以在回答后提供轻量反馈：“太基础 / 正合适 / 跳得太快”，作为**当前概念或命题**下一轮的起点证据。显式反馈优于自动猜测；同一用户换概念时重新判断，不把某一题的状态外推到整个机器学习领域。

建议把这些反馈与已有稳定的 `explanation_preference` 区分：前者是知识位置和讲解校准，后者是“喜欢例子/公式顺序”等表达偏好。仍遵守当前系统提示中的“不宣布学习者已经掌握、不输出掌握度分数”（[`compiler.py`](../recoach-server/app/services/compiler.py)）。前端可只显示“已根据你对这个概念的反馈调整起点”，并允许一键纠正。

### 方向 D：用现有实验基础验证“更会教”，不只验证“更会猜”

论文的 65.7% 是熟练度推断 MAE 相对 IDL 基线的下降，来自同用户 10-shot 设置；3.71 → 4.36 的满意度提升对应**使用用户自报熟练度**的回答。论文另以 16 名回访参与者、522 条满意度评价比较了无熟练度、自报熟练度与预测熟练度的回答，均值分别为 3.586、4.010、3.975（§5.1–5.2）。这些结果不能直接作为知返的预期收益。

知返已有 Event Ledger、运行指标和 Fair Fork，但现有 Fair Fork 只比较 Memory On/Off，不会自动给“教学起点适配”做公平对照。需固定问题、模型、记忆快照和生成参数，对比旧版规则与新版规则。至少看三类结果：起点误判率（按概念分层）、教学效果（能否正确解释或迁移，不只满意度）、成本与体验（首字延迟、纠正率）。成对数据格式、校验和汇总工具见 [教学起点评估说明](teaching-start-evaluation.md)。

## 4. 推荐实施顺序与完成标准

1. **先修正概念模型。** 拆分“目标深度”和“知识起点”，让“详细”只表达回答要求，不自动证明熟练度；缺证据时保持 `unknown` 并沿用现有讲解。完成标准：同一概念的“我是新手但想看完整推导”和“我会链式法则，直接看推导”产生不同前置讲解，同时都满足推导请求。
2. **加入可纠正的局部反馈。** 只对当前概念/命题更新起点证据，记下来源，不写成全局能力标签。完成标准：后续同概念提问会调整，换概念不会错误沿用；用户可覆盖推断。
3. **做受控回放与用户评估。** 先验证 A/B 相比当前版本是否改善理解与适配、是否增加延迟。若没有稳定收益，停止扩展推断链路。

## 参考与核对范围

- 论文：Park, Tark, Gong, [ExPerT: Personalizing LLM Responses to Users’ Domain Expertise via Query-Wise Semantic and Keystroke Behavioral Cues](https://aclanthology.org/2026.acl-long.959/), ACL 2026；本地 PDF 的 §2、§4–5、Limitation、表 1–5。
- 项目：[`README.md`](../README.md)、[`recoach-server/README.md`](../recoach-server/README.md) 及上文链接的真实代码。尚未进行用户实验；上述升级收益均为待验证假设。
