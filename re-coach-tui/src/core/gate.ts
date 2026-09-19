// Clarification Gate（与后端 gate.py 1:1 对齐）
import type {
  ClarificationOption,
  Depth,
  GateDecision,
  GateResult,
  ResolvedTask,
  TaskScope,
} from "../types.js";

const CONCEPT_LEXICON: Record<string, string[]> = {
  deep_learning: [
    "反向传播", "梯度下降", "梯度消失", "梯度爆炸", "链式法则", "激活函数",
    "relu", "sigmoid", "transformer", "注意力", "自注意力", "attention",
    "rope", "旋转位置编码", "位置编码", "flashattention", "flash attention",
    "归一化", "layernorm", "batchnorm", "卷积", "cnn", "rnn", "lstm",
    "dropout", "残差", "resnet", "embedding", "嵌入", "softmax", "交叉熵",
    "学习率", "优化器", "adam", "sgd", "多头注意力", "前馈网络", "损失函数",
  ],
  machine_learning: [
    "线性回归", "逻辑回归", "svm", "支持向量机", "决策树", "随机森林",
    "梯度提升", "xgboost", "聚类", "kmeans", "k-means", "pca", "主成分分析",
    "朴素贝叶斯", "交叉验证", "特征工程", "过拟合", "欠拟合", "正则化",
    "偏差", "方差",
  ],
};

const BROAD_INTENT = /(讲讲|解释|是什么|介绍|不懂|不理解|讲讲看|说说)/;
const SPECIFIC_MARKER =
  /(但|具体|为什么|怎么|如何|区别|关系|已经|知道|理解|先|例如|比如|卡住|不懂的是|不明白的是)/;
const SOCIAL_INTENT = /^(你好|您好|嗨|哈喽|hi|hello|hey|在吗|在不在|谢谢|感谢|多谢|好的|ok|嗯|哦|明白|知道了|拜拜|再见|早上好|中午好|晚上好|晚安|辛苦|麻烦你了|没问题)$/i;

export const MAX_CLARIFY_STREAK = 2;

export function detectConcept(text: string): [string, string] {
  const lowered = text.toLowerCase();
  let bestDomain = "machine_learning";
  let bestConcept = "";
  let bestLen = 0;
  for (const [domain, words] of Object.entries(CONCEPT_LEXICON)) {
    for (const w of words) {
      if (lowered.includes(w.toLowerCase()) && w.length > bestLen) {
        bestDomain = domain;
        bestConcept = w;
        bestLen = w.length;
      }
    }
  }
  return [bestDomain, bestConcept];
}

function detectDepth(text: string): Depth {
  if (/(推导|证明|公式|数学)/.test(text)) return "L4";
  if (/(代码|实现|pytorch|编程|写一个)/i.test(text)) return "L3";
  if (/(直觉|直观|通俗|类比|小白|入门)/.test(text)) return "L1";
  if (/(深入|详细|完整|彻底)/.test(text)) return "L4";
  return "auto";
}

function detectTaskScope(text: string): TaskScope {
  if (/(代码|实现|pytorch|编程)/i.test(text)) return "代码实现";
  if (/(推导|证明|公式)/.test(text)) return "数学推导";
  if (/论文/.test(text)) return "论文理解";
  if (/(机制|原理|为什么|怎么做到|内部)/.test(text)) return "机制分析";
  if (/(工程|权衡|部署|性能|显存|加速)/.test(text)) return "工程权衡";
  return "直觉解释";
}

function detectOutputPreferences(text: string): string[] {
  const prefs: string[] = [];
  const patterns: Array<[RegExp, string]> = [
    [/(只要|只看|先给|直接给).{0,6}(代码|实现)/, "优先代码，弱化推导"],
    [/(先|只要|直接).{0,6}(公式|推导)/, "先给公式或推导"],
    [/(不要|不用|先别).{0,4}(公式|数学)/, "避免公式，先讲直觉"],
    [/(不要|不用).{0,4}代码/, "不要代码"],
    [/(数值|数字).{0,4}(例子|优先|先)/, "数值例子优先"],
    [/(简短|简洁|控制在|分钟内|三句话|一段话)/, "控制篇幅，短段讲解"],
    [/(英文|英语)/, "用英文回答"],
  ];
  for (const [pattern, label] of patterns) {
    if (pattern.test(text)) prefs.push(label);
  }
  return prefs;
}

function buildPlan(task: ResolvedTask): string[] {
  const plan = ["定位这个概念解决的问题", "连接你已知的部分"];
  if (task.desiredDepth === "L0" || task.desiredDepth === "L1" || task.taskScope === "直觉解释") {
    plan.push("用最小直觉讲清楚", "说明类比的边界");
  } else if (task.taskScope === "代码实现") {
    plan.push("给出最小代码示例", "逐行对应概念解释");
  } else if (task.taskScope === "数学推导") {
    plan.push("从定义出发推导", "解释每一步的含义");
  } else {
    plan.push("建立最小直觉", "展开机制与因果", "用数值或例子落地");
  }
  if (task.outputPreference.includes("控制篇幅，短段讲解")) {
    plan.push("压缩为短段");
  }
  return plan.slice(0, 5);
}

function clarificationOptions(concept: string): ClarificationOption[] {
  const topic = concept || "这个问题";
  return [
    { id: "intuition", label: "整体直觉", detail: `想先建立${topic}的整体直觉`, followUp: `我想先建立${topic}的整体直觉。用简单的例子，先不要公式。` },
    { id: "mechanism", label: "机制细节", detail: "大概知道是什么，但不理解内部怎么运作", followUp: `我已经知道${topic}大概是什么，但不理解它内部具体怎么运作。` },
    { id: "math", label: "公式推导", detail: "想看定义、公式和推导", followUp: `我想看${topic}的公式和推导，从定义开始。` },
    { id: "code", label: "代码实现", detail: "想看最小代码示例", followUp: `我想看${topic}怎么用代码实现，给一个最小示例。` },
    { id: "map", label: "还不确定", detail: "先要一张短小的概念地图", followUp: `我还不确定${topic}卡在哪里。先给我一张短小的概念地图，再让我选。` },
  ];
}

export interface RunGateArgs {
  clarifyStreak: number;
  knownContext?: string[];
}

export function runGate(userText: string, args: RunGateArgs): GateResult {
  const text = userText.trim();
  const compact = text.replace(/[\s，。？！,.?!、：:；;]/g, "");
  const contextItems = args.knownContext ?? [];
  let [domain, concept] = detectConcept(text);
  if (!concept && contextItems.length > 0) {
    const [contextDomain, contextConcept] = detectConcept(contextItems.join(" "));
    if (contextConcept) {
      domain = contextDomain;
      concept = contextConcept;
    }
  }
  const depth = detectDepth(text);
  const scope = detectTaskScope(text);
  const prefs = detectOutputPreferences(text);

  const task: ResolvedTask = {
    goal: text.slice(0, 120),
    domain,
    concept,
    proposition: text.slice(0, 200),
    knownContext: contextItems.slice(-4),
    desiredDepth: depth,
    taskScope: scope,
    outputPreference: prefs,
    openQuestions: [],
    assumptions: [],
  };

  let focus = concept ? `${concept} · ${scope}` : `当前问题 · ${scope}`;
  const base: GateResult = {
    decision: "READY",
    task,
    focus,
    plan: buildPlan(task),
  };

  if (SOCIAL_INTENT.test(compact) && !concept) {
    task.taskScope = "寒暄与开场";
    base.focus = "寒暄与开场";
    base.plan = ["友好回应", "引导学习者提出想学的概念"];
    return base;
  }

  if (args.clarifyStreak >= MAX_CLARIFY_STREAK) {
    base.decision = "ANSWER_WITH_ASSUMPTION";
    base.assumption = "信息仍不完整，按最常见的学习场景陈述假设后继续讲解。";
    task.assumptions.push(base.assumption);
    return base;
  }

  const broad = BROAD_INTENT.test(compact);
  const specific =
    SPECIFIC_MARKER.test(compact) || prefs.length > 0 || Boolean(concept && compact.length > 24);
  const confused =
    /(我不懂|我不理解|没搞懂|没听懂|卡在|卡住|不明白|不懂的是|不理解的是)/.test(compact);

  if (compact.length <= 8 && broad && !specific) {
    base.decision = "NEEDS_CLARIFICATION";
  } else if (compact.length <= 14 && broad && !specific && !concept) {
    base.decision = "NEEDS_CLARIFICATION";
  } else if (confused && !specific && compact.length <= 40) {
    base.decision = "NEEDS_CLARIFICATION";
  } else if (isNonInformative(compact)) {
    // 纯编号/符号（"1"、"A"、"???"）没有任何语义内容。
    // 之前这类输入因为不含 broad 词而直接放行，导致发一个"1"就换来一整篇泛泛讲解。
    base.decision = "NEEDS_CLARIFICATION";
  }

  if (base.decision === "NEEDS_CLARIFICATION") {
    base.plan = ["判断缺失信息是否会改变讲解路线", "只提出一个高信息量问题"];
    if (isNonInformative(compact)) {
      // 输入本身没有语义，此时问"你卡在哪"没有意义——用户根本没说想问什么。
      base.focus = "等待明确的问题";
      base.plan = ["说明没看懂这条输入", "给出几个可以直接开始的入口"];
      base.question =
        "这条消息里只有编号或符号，我没看出你想聊哪个概念。\n\n" +
        "如果你是在回应上一轮的选项，可以直接选，或回复选项前的字母（如 A）。\n" +
        "也可以直接告诉我你想弄懂什么，比如：";
      base.options = starterOptions();
      return base;
    }
    const topic = concept || "这个概念";
    base.focus = "定位真实卡点";
    base.question =
      `先确认一个会显著改变讲解路线的点：关于「${topic}」，你目前最接近哪种情况？\n\n` +
      "点选项，或直接回复前面的字母/编号都可以（例如 A 或 1）。";
    base.options = clarificationOptions(concept);
    return base;
  }

  return base;
}

// 有语义内容的字符：中日韩文字，或 2 个以上拉丁字母组成的"词"。
// 纯数字单独看不算语义（"1" 是编号，不是概念）。
const SEMANTIC = /[㐀-䶿一-鿿豈-﫿]|[A-Za-z]{2,}/;

/**
 * 输入是否没有任何语义内容（纯编号、纯符号、单个字母）。
 *
 * 判定必须基于**原始文本**而不是补全后的 concept：
 * concept 会从会话上下文继承，所以"1"在聊过反向传播的会话里
 * 也会被解析出 concept，从而被误当成有效提问。
 */
export function isNonInformative(compact: string): boolean {
  if (!compact) return true;
  const stripped = compact.replace(
    /[0-9A-Za-z\s，。？！,.?!、：:；;（）()\[\]{}【】\-—_/\\|~`'\"*+#@$%^&<>]/g,
    "",
  );
  if (stripped) return false;
  return !SEMANTIC.test(compact);
}

/** 输入无法理解时给出的入门入口，让用户一键就能开始。 */
function starterOptions(): ClarificationOption[] {
  return [
    {
      id: "gradient",
      label: "梯度下降",
      detail: "为什么沿负梯度方向走能降低损失",
      followUp:
        "我想先建立梯度下降的整体直觉：为什么沿负梯度方向走能降低损失。用简单的例子，先不要公式。",
    },
    {
      id: "backprop",
      label: "反向传播",
      detail: "梯度是怎么一层层传回去的",
      followUp:
        "我想先建立反向传播的整体直觉：梯度是怎么一层层传回去的。用简单的例子，先不要公式。",
    },
    {
      id: "overfit",
      label: "过拟合与正则化",
      detail: "为什么模型会记住训练集",
      followUp:
        "我想先建立过拟合与正则化的整体直觉：为什么模型会记住训练集。用简单的例子，先不要公式。",
    },
  ];
}
