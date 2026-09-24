from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schemas import ClarificationOption, GateDecision, ResolvedTask

# 概念词典只用于作用域对齐与检索过滤，不影响讲解内容本身。
CONCEPT_LEXICON: dict[str, list[str]] = {
    "deep_learning": [
        "反向传播", "梯度下降", "梯度消失", "梯度爆炸", "链式法则", "激活函数",
        "relu", "sigmoid", "transformer", "注意力", "自注意力", "attention",
        "rope", "旋转位置编码", "位置编码", "flashattention", "flash attention",
        "归一化", "layernorm", "batchnorm", "卷积", "cnn", "rnn", "lstm",
        "dropout", "残差", "resnet", "embedding", "嵌入", "softmax", "交叉熵",
        "学习率", "优化器", "adam", "sgd", "多头注意力", "前馈网络", "损失函数",
    ],
    "machine_learning": [
        "线性回归", "逻辑回归", "svm", "支持向量机", "决策树", "随机森林",
        "梯度提升", "xgboost", "聚类", "kmeans", "k-means", "pca", "主成分分析",
        "朴素贝叶斯", "交叉验证", "特征工程", "过拟合", "欠拟合", "正则化",
        "偏差", "方差",
    ],
}

_BROAD_INTENT = re.compile(r"(讲讲|解释|是什么|介绍|不懂|不理解|讲讲看|说说)")
_SPECIFIC_MARKER = re.compile(
    r"(但|具体|为什么|怎么|如何|区别|关系|已经|知道|理解|先|例如|比如|卡住|不懂的是|不明白的是)"
)
# 寒暄/开场：不是教学请求。若不单独标记，会套用默认 task_scope="直觉解释"，
# 模型据此脑补教学偏好（如"避免公式，用直觉"），思考链出现与记忆无关的偏好内容。
_SOCIAL_INTENT = re.compile(
    r"^(你好|您好|嗨|哈喽|hi|hello|hey|在吗|在不在|谢谢|感谢|多谢|好的|ok|嗯|哦|"
    r"明白|知道了|拜拜|再见|早上好|中午好|晚上好|晚安|辛苦|麻烦你了|没问题)$",
    re.IGNORECASE,
)

MAX_CLARIFY_STREAK = 2  # v0.6：最多连续澄清两轮


@dataclass
class GateResult:
    decision: GateDecision
    task: ResolvedTask
    focus: str
    plan: list[str]
    question: str = ""
    options: list[ClarificationOption] = field(default_factory=list)
    assumption: str = ""


def _detect_concept(text: str) -> tuple[str, str]:
    lowered = text.lower()
    best_domain, best_concept, best_len = "machine_learning", "", 0
    for domain, words in CONCEPT_LEXICON.items():
        for w in words:
            if w.lower() in lowered and len(w) > best_len:
                best_domain, best_concept, best_len = domain, w, len(w)
    return best_domain, best_concept


def _detect_depth(text: str) -> str:
    if re.search(r"(推导|证明|公式|数学)", text):
        return "L4"
    if re.search(r"(代码|实现|pytorch|编程|写一个)", text, re.IGNORECASE):
        return "L3"
    if re.search(r"(直觉|直观|通俗|类比|小白|入门)", text):
        return "L1"
    if re.search(r"(深入|详细|完整|彻底)", text):
        return "L3"
    return "auto"


def _detect_task_scope(text: str) -> str:
    if re.search(r"(代码|实现|pytorch|编程)", text, re.IGNORECASE):
        return "代码实现"
    if re.search(r"(推导|证明|公式)", text):
        return "数学推导"
    if re.search(r"论文", text):
        return "论文理解"
    if re.search(r"(机制|原理|为什么|怎么做到|内部)", text):
        return "机制分析"
    if re.search(r"(工程|权衡|部署|性能|显存|加速)", text):
        return "工程权衡"
    return "直觉解释"


def _detect_output_preferences(text: str) -> list[str]:
    prefs: list[str] = []
    patterns = [
        (r"(只要|只看|先给|直接给).{0,6}(代码|实现)", "优先代码，弱化推导"),
        (r"(先|只要|直接).{0,6}(公式|推导)", "先给公式或推导"),
        (r"(不要|不用|先别).{0,4}(公式|数学)", "避免公式，先讲直觉"),
        (r"(不要|不用).{0,4}代码", "不要代码"),
        (r"(数值|数字).{0,4}(例子|优先|先)", "数值例子优先"),
        (r"(简短|简洁|控制在|分钟内|三句话|一段话)", "控制篇幅，短段讲解"),
        (r"(英文|英语)", "用英文回答"),
    ]
    for pattern, label in patterns:
        if re.search(pattern, text):
            prefs.append(label)
    return prefs


def _build_plan(task: ResolvedTask) -> list[str]:
    """用户可见的简短行动摘要（确定性生成，不是模型思维链）。"""
    plan = ["定位这个概念解决的问题", "连接你已知的部分"]
    if task.desired_depth in ("L0", "L1") or task.task_scope == "直觉解释":
        plan += ["用最小直觉讲清楚", "说明类比的边界"]
    elif task.task_scope == "代码实现":
        plan += ["给出最小代码示例", "逐行对应概念解释"]
    elif task.task_scope == "数学推导":
        plan += ["从定义出发推导", "解释每一步的含义"]
    else:
        plan += ["建立最小直觉", "展开机制与因果", "用数值或例子落地"]
    if "控制篇幅，短段讲解" in task.output_preference:
        plan.append("压缩为短段")
    return plan[:5]


# 有语义内容的字符：中日韩文字或拉丁字母/数字组成的"词"。
# 纯数字单独看不算语义（"1" 是编号，不是概念），因此数字只有在与其它
# 内容组合时才计入。
_SEMANTIC = re.compile(r"[㐀-䶿一-鿿豈-﫿]|[A-Za-z]{2,}")


def _is_non_informative(compact: str) -> bool:
    """输入是否没有任何语义内容（纯编号、纯符号、单个字母）。

    判定必须基于**原始文本**而不是补全后的 concept：
    concept 会从会话上下文继承，所以"1"在聊过反向传播的会话里
    也会被解析出 concept，从而被误当成有效提问。
    """
    if not compact:
        return True
    # 去掉所有编号/标点后若什么都不剩，就是纯选择符或纯符号
    stripped = re.sub(r"[0-9A-Za-z\s，。？！,.?!、：:；;（）()\[\]{}【】\-—_/\\|~`'\"*+#@$%^&<>]", "", compact)
    if stripped:
        return False
    # 剩下的全是编号/字母/符号：只有当其中不含"成词"的内容时才算非信息性
    return not _SEMANTIC.search(compact)


def _starter_options() -> list[ClarificationOption]:
    """输入无法理解时给出的入门入口，让用户一键就能开始。"""
    return [
        ClarificationOption(
            id="gradient",
            label="梯度下降",
            detail="为什么沿负梯度方向走能降低损失",
            followUp="我想先建立梯度下降的整体直觉：为什么沿负梯度方向走能降低损失。用简单的例子，先不要公式。",
        ),
        ClarificationOption(
            id="backprop",
            label="反向传播",
            detail="梯度是怎么一层层传回去的",
            followUp="我想先建立反向传播的整体直觉：梯度是怎么一层层传回去的。用简单的例子，先不要公式。",
        ),
        ClarificationOption(
            id="overfit",
            label="过拟合与正则化",
            detail="为什么模型会记住训练集",
            followUp="我想先建立过拟合与正则化的整体直觉：为什么模型会记住训练集。用简单的例子，先不要公式。",
        ),
    ]


def _clarification_options(concept: str) -> list[ClarificationOption]:
    topic = concept or "这个问题"
    return [
        ClarificationOption(
            id="intuition",
            label="整体直觉",
            detail=f"想先建立{topic}的整体直觉",
            followUp=f"我想先建立{topic}的整体直觉。用简单的例子，先不要公式。",
        ),
        ClarificationOption(
            id="mechanism",
            label="机制细节",
            detail=f"大概知道是什么，但不理解内部怎么运作",
            followUp=f"我已经知道{topic}大概是什么，但不理解它内部具体怎么运作。",
        ),
        ClarificationOption(
            id="math",
            label="公式推导",
            detail="想看定义、公式和推导",
            followUp=f"我想看{topic}的公式和推导，从定义开始。",
        ),
        ClarificationOption(
            id="code",
            label="代码实现",
            detail="想看最小代码示例",
            followUp=f"我想看{topic}怎么用代码实现，给一个最小示例。",
        ),
        ClarificationOption(
            id="map",
            label="还不确定",
            detail="先要一张短小的概念地图",
            followUp=f"我还不确定{topic}卡在哪里。先给我一张短小的概念地图，再让我选。",
        ),
    ]


def run_gate(user_text: str, *, clarify_streak: int, known_context: list[str] | None = None) -> GateResult:
    """Clarification Gate（v0.6 §6.3）。

    只在缺失信息会改变讲解路线时提问；连续两轮澄清后必须以显式假设继续。
    P0 使用确定性启发式，保证可测试、无额外模型延迟；接口预留 LLM 判定位。
    """
    text = user_text.strip()
    compact = re.sub(r"[\s，。？！,.?!、：:；;]", "", text)
    context_items = known_context or []
    social = bool(_SOCIAL_INTENT.search(compact))
    domain, concept = _detect_concept(text)
    if not concept and context_items and not social:
        context_domain, context_concept = _detect_concept(" ".join(context_items))
        if context_concept:
            domain, concept = context_domain, context_concept
    depth = _detect_depth(text)
    scope = _detect_task_scope(text)
    prefs = _detect_output_preferences(text)

    task = ResolvedTask(
        goal=text[:120],
        domain=domain,
        concept=concept,
        proposition=text[:200],
        known_context=context_items[-4:],
        desired_depth=depth,
        task_scope=scope,
        output_preference=prefs,
    )

    focus = f"{concept} · {scope}" if concept else f"当前问题 · {scope}"
    base = GateResult(decision="READY", task=task, focus=focus, plan=_build_plan(task))

    # 寒暄/开场消息：不是教学请求，不套教学模板。
    if social:
        task.task_scope = "寒暄与开场"
        base.focus = "寒暄与开场"
        base.plan = ["友好回应", "引导学习者提出想学的概念"]
        return base

    # 连续两轮澄清后不再追问：显式假设 + best-effort 解释
    if clarify_streak >= MAX_CLARIFY_STREAK:
        base.decision = "ANSWER_WITH_ASSUMPTION"
        base.assumption = "信息仍不完整，按最常见的学习场景陈述假设后继续讲解。"
        task.assumptions.append(base.assumption)
        return base

    broad = bool(_BROAD_INTENT.search(compact))
    specific = bool(_SPECIFIC_MARKER.search(compact)) or bool(prefs) or bool(concept and len(compact) > 24)
    # 困惑声明（"我不懂 X"/"不理解 X"/"卡在 X"）：只给了概念、没给卡点
    # （缺的是定义、关系、机制还是因果），即便概念明确也应先澄清。
    # 字符数阈值对英文长概念词（如 flashattention=13 字符）不公平，
    # 不加此判定时"我不懂flashattention"会被放行全量讲解，思考链又长又无针对性。
    confused = bool(re.search(r"(我不懂|我不理解|没搞懂|没听懂|卡在|卡住|不明白|不懂的是|不理解的是)", compact))

    if len(compact) <= 8 and broad and not specific:
        base.decision = "NEEDS_CLARIFICATION"
    elif len(compact) <= 14 and broad and not specific and not concept:
        base.decision = "NEEDS_CLARIFICATION"
    elif confused and not specific and len(compact) <= 40:
        base.decision = "NEEDS_CLARIFICATION"
    elif _is_non_informative(compact):
        # 纯编号/符号（"1"、"A"、"???"）没有任何语义内容。
        # 之前这类输入因为不含 broad 词而直接放行，导致发一个"1"
        # 就换来一整篇泛泛讲解（实测最长 3202 字符）。
        base.decision = "NEEDS_CLARIFICATION"

    if base.decision == "NEEDS_CLARIFICATION":
        base.focus = "定位真实卡点"
        base.plan = ["判断缺失信息是否会改变讲解路线", "只提出一个高信息量问题"]
        if _is_non_informative(compact):
            # 输入本身没有语义（"1"/"A"/"???"），此时问"你卡在哪"没有意义——
            # 用户根本没说想问什么。直接说清没看懂，并给出可选的入口。
            base.focus = "等待明确的问题"
            base.plan = ["说明没看懂这条输入", "给出几个可以直接开始的入口"]
            base.question = (
                "这条消息里只有编号或符号，我没看出你想聊哪个概念。\n\n"
                "如果你是在回应上一轮的选项，可以直接点选项，或回复选项前的字母（如 A）。\n"
                "也可以直接告诉我你想弄懂什么，比如："
            )
            base.options = _starter_options()
            return base
        topic = concept or "这个概念"
        base.question = (
            f"先确认一个会显著改变讲解路线的点：关于「{topic}」，你目前最接近哪种情况？\n\n"
            "点选项，或直接回复前面的字母/编号都可以（例如 A 或 1）。"
        )
        base.options = _clarification_options(concept)
        return base

    return base
