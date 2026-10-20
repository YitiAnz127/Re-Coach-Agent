import { createInterface } from "node:readline/promises";
import { Writable } from "node:stream";
import type { AuthInteraction } from "@earendil-works/pi-ai";
import { sanitizeTerminalText } from "./ui/sanitize.js";

export function cliAuthInteraction(controller: AbortController): AuthInteraction {
  const writeLine = (s: string) => process.stdout.write(sanitizeTerminalText(s) + "\n");
  return {
    signal: controller.signal,
    notify(event) {
      if (event.type === "auth_url") writeLine(`在浏览器打开：\n${event.url}\n${event.instructions ?? ""}`);
      else if (event.type === "device_code") writeLine(`打开 ${event.verificationUri}\n输入设备码：${event.userCode}`);
      else {
        writeLine(event.message);
        if (event.type === "info") for (const link of event.links ?? []) writeLine(`${link.label ?? "说明"}: ${link.url}`);
      }
    },
    async prompt(p) {
      const signal = p.signal ? AbortSignal.any([controller.signal, p.signal]) : controller.signal;
      signal.throwIfAborted();
      writeLine(p.message);
      if (p.type === "select") p.options.forEach((o, i) => writeLine(`${i + 1}. ${o.label} (${o.id})`));
      const secret = p.type === "secret" || p.type === "manual_code";
      // readline owns raw input in a TTY. Suppress its echo for keys and callback codes.
      const output = new Writable({ write(chunk, _encoding, done) {
        if (!secret) process.stdout.write(chunk); done();
      } });
      process.stdout.write(secret ? "> （输入不回显）" : "> ");
      const rl = createInterface({ input: process.stdin, output, terminal: Boolean(process.stdin.isTTY) });
      rl.on("SIGINT", () => controller.abort());
      try {
        const value = (await rl.question("", { signal })).trim();
        if (p.type !== "select") return value;
        const option = p.options.find(o => o.id === value) ?? p.options[Number(value) - 1];
        if (!option) throw new Error("无效选择。");
        return option.id;
      } finally { rl.close(); output.destroy(); if (secret) process.stdout.write("\n"); }
    },
  };
}
