import type {
  ClarificationOption,
  PerformanceMetrics,
  TurnPresentation,
} from "../types";

export interface DemoReply {
  content: string;
  presentation: TurnPresentation;
  usesTool: boolean;
}

const defaultMetrics: PerformanceMetrics = {
  timeToFirstTokenMs: 612,
  memorySearchMs: 8,
  contextCompileMs: 3,
  memoryCapsuleTokens: 132,
  totalInputTokens: 2080,
};

const clarificationOptions: ClarificationOption[] = [
  {
    id: "intuition",
    label: "整体直觉",
    detail: "不知道反向传播整体在做什么",
    followUp: "我知道导数和梯度下降，但想先建立反向传播的整体直觉。先用数值，不要上矩阵公式。",
  },
  {
    id: "chain-rule",
    label: "梯度怎样传递",
    detail: "不明白每层的局部梯度怎样连起来",
    followUp: "我知道导数和梯度下降，但不理解每层的梯度怎样通过链式法则连起来。先用标量数值。",
  },
  {
    id: "graph",
    label: "计算图",
    detail: "看不懂节点和边如何参与求导",
    followUp: "我想从计算图理解反向传播：节点和边分别怎样参与求导？",
  },
  {
    id: "code",
    label: "代码实现",
    detail: "不知道 loss.backward() 实际执行了什么",
    followUp: "我理解基本求导，但不知道 PyTorch 的 loss.backward() 在计算图里实际执行了什么。",
  },
  {
    id: "map",
    label: "还不确定",
    detail: "先给我一张短小的概念地图",
    followUp: "我还不确定反向传播具体卡在哪里。先给我一张短小的概念地图，再让我选。",
  },
];

function isBroadQuestion(input: string) {
  const compact = input.replace(/[\s，。？！,.?!]/g, "");
  return (
    compact.length <= 13 &&
    /(不懂|讲讲|解释|是什么).*(反向传播|梯度下降|注意力|attention|rope)/i.test(compact)
  );
}

function clarificationReply(): DemoReply {
  return {
    content:
      "先确认一个会显著改变讲解路线的点：你现在最卡在哪一层？\n\n只选最接近的一项就好，我会从那里开始。",
    usesTool: false,
    presentation: {
      mode: "clarify",
      focus: "定位真实卡点",
      plan: ["判断缺失信息是否会改变讲解路线", "只提出一个高信息量问题"],
      personalization: [],
      metrics: {
        ...defaultMetrics,
        timeToFirstTokenMs: 184,
        memorySearchMs: 0,
        contextCompileMs: 1,
        memoryCapsuleTokens: 0,
        totalInputTokens: 724,
      },
      clarificationOptions,
      suggestedActions: [],
    },
  };
}

function gradientReply(): DemoReply {
  return {
    content:
      "先看一个最小模型。反向传播不是“把答案往回送”，而是在计算：最终损失对每个中间变量的变化有多敏感。\n\n假设一条路径上的每个局部导数都是 0.5。离 loss 越远，梯度就要乘越多次 0.5：两层后是 0.25，十层后只剩约 0.001。\n\n这就是关键连接：链式法则负责把局部导数组合起来；当许多绝对值小于 1 的局部导数连续相乘，前层收到的更新信号会快速变弱。梯度消失不是反向传播之外的另一件事，而是它沿长路径组合导数时可能出现的结果。",
    usesTool: true,
    presentation: {
      mode: "explain",
      depth: "L3",
      focus: "局部梯度如何累积成总梯度",
      plan: [
        "从标量连乘建立直觉",
        "运行一个梯度衰减实验",
        "连接到链式法则",
        "最后引入简化公式",
      ],
      personalization: [
        {
          memoryId: "mem_numeric_before_formula",
          label: "数值优先",
          scope: "深度学习 · 讲解偏好",
          effect: "先展示 0.5 连乘的数值变化，再连接链式法则。",
        },
        {
          memoryId: "mem_short_chunks",
          label: "短段讲解",
          scope: "全局 · 交互规则",
          effect: "将机制拆成三个短段，公式延后。",
        },
      ],
      metrics: defaultMetrics,
      experiment: {
        title: "局部导数连乘",
        description: "固定每层局部导数为 0.5，观察路径变深时总梯度的变化。",
        code: "for depth in [2, 5, 10, 20]:\n    print(depth, 0.5 ** depth)",
        points: [
          { label: "2 层", value: 0.25, displayValue: "0.25" },
          { label: "5 层", value: 0.03125, displayValue: "0.031" },
          { label: "10 层", value: 0.0009766, displayValue: "0.00098" },
          { label: "20 层", value: 0.0000009537, displayValue: "9.5e−7" },
        ],
        takeaway: "路径每增加一层，信号继续乘以 0.5；深层因此更难收到足够大的更新。",
      },
      suggestedActions: [
        {
          id: "connect-chain-rule",
          label: "连接到链式法则",
          prompt: "把这个数值直觉连接到链式法则，但先只用标量公式。",
        },
        {
          id: "compare-residual",
          label: "残差连接怎样帮助",
          prompt: "沿用这个例子，解释残差连接为什么能缓解梯度消失。",
        },
        {
          id: "unit-done",
          label: "先停在这里",
          prompt: "我理解这部分了，先停在这里并做本轮复盘。",
        },
      ],
    },
  };
}

function ropeReply(): DemoReply {
  return {
    content:
      "可以把 RoPE 先看成一组转速不同的二维时钟。每两个维度组成一个平面；位置 p 不直接加到向量上，而是让这个平面里的向量旋转 pω 的角度。\n\n不同维度对使用不同的频率 ω：快钟区分相近位置，慢钟保留更长距离的变化。查询向量和键向量都这样旋转后，它们的内积会自然带上相对位置差 p−q。\n\n这个类比的边界是：时钟只是帮助看懂多频率旋转，不代表模型真的在读取时间。真正保留下来的是旋转矩阵与内积共同产生的相对位置信息。",
    usesTool: false,
    presentation: {
      mode: "explain",
      depth: "L3",
      focus: "RoPE 的多频率旋转直觉",
      plan: ["建立二维旋转直觉", "解释多频率分工", "连接到相对位置", "指出时钟类比的边界"],
      personalization: [
        {
          memoryId: "mem_intuition_before_formula",
          label: "直觉先行",
          scope: "Transformer · 讲解偏好",
          effect: "先使用多组时钟建立旋转直觉，公式只保留 pω。",
        },
      ],
      metrics: { ...defaultMetrics, timeToFirstTokenMs: 548, memoryCapsuleTokens: 84 },
      suggestedActions: [
        { id: "rope-formula", label: "展开旋转公式", prompt: "现在展开 RoPE 的二维旋转公式。" },
        { id: "rope-relative", label: "证明相对位置", prompt: "推导为什么 QK 内积只和相对位置有关。" },
        { id: "rope-boundary", label: "看长上下文边界", prompt: "继续讲 RoPE 在长上下文扩展里的边界。" },
      ],
    },
  };
}

function flashAttentionReply(): DemoReply {
  return {
    content:
      "FlashAttention 没有改变 Attention 需要比较所有 token 对的事实，所以计算量仍是 O(n²)。它改变的是这些计算怎样在硬件上执行。\n\n普通实现会把完整的 n×n 注意力矩阵反复写入和读出显存；FlashAttention 按块把 Q、K、V 搬进更快的片上 SRAM，在块内完成计算，并用在线 softmax 合并结果。\n\n因此它减少的是昂贵的显存读写，而不是 token 对的数量。可以把结论记成：同样多的数学工作，更少地来回搬数据。",
    usesTool: false,
    presentation: {
      mode: "explain",
      depth: "L3",
      focus: "计算复杂度与 IO 复杂度的区别",
      plan: ["先区分计算与数据搬运", "比较两种内存访问路径", "解释分块与在线 softmax", "回到 O(n²) 边界"],
      personalization: [],
      metrics: { ...defaultMetrics, timeToFirstTokenMs: 571, memoryCapsuleTokens: 0 },
      suggestedActions: [
        { id: "flash-memory", label: "画出内存路径", prompt: "用一张简图比较普通 Attention 与 FlashAttention 的内存路径。" },
        { id: "flash-softmax", label: "解释在线 softmax", prompt: "用小数值例子解释分块后如何正确计算在线 softmax。" },
      ],
    },
  };
}

function retrospectiveReply(): DemoReply {
  return {
    content: "好，我们先停在这里。我只记录本轮可观察到的连接与开放问题，不替你判断是否已经学会。",
    usesTool: false,
    presentation: {
      mode: "reflect",
      focus: "梯度传播 · 知识单元复盘",
      plan: ["整理本轮已建立的连接", "保留仍未展开的问题", "记录本次有效讲法"],
      personalization: [
        {
          memoryId: "mem_numeric_before_formula",
          label: "数值优先",
          scope: "深度学习 · 讲解偏好",
          effect: "本轮先用连乘数值，再连接公式。",
        },
        {
          memoryId: "mem_short_chunks",
          label: "短段讲解",
          scope: "全局 · 交互规则",
          effect: "机制被拆成短段，没有一次展开完整矩阵推导。",
        },
      ],
      metrics: { ...defaultMetrics, timeToFirstTokenMs: 392, totalInputTokens: 1684 },
      retrospective: {
        connections: [
          "反向传播沿路径组合局部导数。",
          "连续乘上绝对值小于 1 的局部导数，会让前层梯度快速变小。",
          "梯度消失是链式法则沿长路径应用时可能出现的结果。",
        ],
        openQuestions: ["ReLU、初始化与残差连接分别怎样改变梯度传播。"],
        approach: ["先用标量数值，再连接机制。", "拆成短段，矩阵公式延后。"],
      },
      suggestedActions: [
        { id: "next-residual", label: "下次从残差连接继续", prompt: "从残差连接怎样帮助梯度传播继续。" },
      ],
    },
  };
}

export function getDemoReply(input: string): DemoReply {
  if (/(停在这里|做本轮复盘|先到这里|我理解这部分了)/i.test(input)) {
    return retrospectiveReply();
  }

  if (isBroadQuestion(input)) {
    return clarificationReply();
  }

  if (/rope|旋转|时钟|位置编码/i.test(input)) {
    return ropeReply();
  }

  if (/flashattention|flash attention|显存|复杂度/i.test(input)) {
    return flashAttentionReply();
  }

  return gradientReply();
}
