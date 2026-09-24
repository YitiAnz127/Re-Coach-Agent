# 教学起点适配的对照评估

本仓库已提供逐问教学起点与概念级反馈。评估脚本 `tools/evaluate_teaching_start.py` 只汇总**实际收集并人工标注**的成对数据，不调用模型，也不把 ExPerT 的实验结果当作知返的收益。

## 成对收集

对同一问题生成两个回答：`baseline` 使用旧版教学起点规则，`adaptive` 使用新版规则。两臂固定同一模型、生成参数、当前问题和记忆/命题快照；只有教学起点策略变化。保留原回答，盲评其正确性、术语负担和迁移题表现。满意度与迁移题由同一评估者给两臂打分；顺序随机化。不要直接用已有的 Memory On/Off Fair Fork 代替此对照，因为它同时改变记忆条件。

每行一个 JSON 对象，包含唯一 `case_id`、原始 `query`、`baseline`、`adaptive`。两臂须保存各自的 `response` 与 `policy_version`，并有一致的 `model`、`memory_snapshot`、`generation_config` 与有效的 `start`。有人工起点标注时填 `gold_start`；回答满意度（1–5）、迁移题对错和首字延迟可分别填 `satisfaction`、`transfer_correct`、`ttft_ms`。缺失指标会按可用样本计数，不补零。

```json
{"case_id":"匿名案例01","query":"请解释反向传播的链式法则","scenario":"cold_start","gold_start":"novice","baseline":{"model":"固定模型","memory_snapshot":"同一快照ID","generation_config":{"temperature":0},"policy_version":"旧版策略","start":"unknown","response":"旧版回答全文","satisfaction":3,"transfer_correct":false,"ttft_ms":1200},"adaptive":{"model":"固定模型","memory_snapshot":"同一快照ID","generation_config":{"temperature":0},"policy_version":"policy_1.2.0","start":"novice","response":"新版回答全文","satisfaction":4,"transfer_correct":true,"ttft_ms":1250}}
```

上行仅展示数据格式，不能作为实验数据提交。建议按 `cold_start`、`same_concept`、`concept_switch` 分组，并记录中文输入、移动端等使用条件；论文数字不能直接外推。对于“太基础/正合适/跳得太快”的线上反馈，先看各概念的纠正率，再收集能回答迁移问题的人工评估；满意度只能作为其中一个结果。

运行：

```powershell
python tools/evaluate_teaching_start.py path/to/paired-cases.jsonl --output path/to/report.json
```

脚本报告起点准确率、起点过高率、迁移题正确率、成对满意度差值和首字延迟中位数，并为每项保留实际样本量。它不生成回答、不判断科学正确性，也不自动进行显著性检验。只有完成真实对照收集后，才能据此判断升级是否优于当前版本。
