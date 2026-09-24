// 短随机 ID 生成（与后端 ids.py 语义一致）
//
// 用 crypto 而不是 Math.random：这些 ID 目前只做本地标识、不作为凭证，
// 但临时文件名（store.ts 的 `${file}.${newTurnId()}.tmp`）也用同一套生成器，
// 可预测的文件名在共享目录下是竞态/抢占面。crypto 无额外成本，直接换掉。
import { randomBytes } from "node:crypto";

const ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

export function randomId(length = 10): string {
  // 拒绝采样消除取模偏置（36 不整除 256）
  const limit = Math.floor(256 / ALPHABET.length) * ALPHABET.length;
  let out = "";
  while (out.length < length) {
    for (const byte of randomBytes(length * 2)) {
      if (byte >= limit) continue;
      out += ALPHABET[byte % ALPHABET.length];
      if (out.length === length) break;
    }
  }
  return out;
}

export function newTurnId(): string {
  return `turn_${randomId(10)}`;
}

export function newMemoryId(): string {
  return `mem_${randomId(10)}`;
}

export function newSessionId(): string {
  return `sess_${randomId(12)}`;
}

/**
 * 事件 id。同样必须走 crypto。
 *
 * 事件 id 不只用于展示：它会作为 `sourceEventIds` 写进记忆，并参与
 * writeMemory / forgetMemories 的**幂等匹配**。撞了会让一条新记忆被当成
 * "已写过"而静默跳过，或让一次遗忘重放错的对象。
 */
export function newEventId(): string {
  return `evt_${randomId(10)}`;
}
