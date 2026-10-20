import { Container, CURSOR_MARKER, Input, SelectList, Text, type TUI } from "@earendil-works/pi-tui";
import type { AuthInteraction } from "@earendil-works/pi-ai";
import { sanitizeTerminalText } from "./sanitize.js";
import { selectListTheme } from "./theme.js";

/** Never call Input.render for secrets: no raw value reaches the terminal renderer. */
export class SecretInput extends Input {
  override render(width: number): string[] {
    return ["> " + "•".repeat(Math.min(Array.from(this.getValue()).length, Math.max(0, width - 4)))
      + (this.focused ? CURSOR_MARKER : "")];
  }
}

export function createAuthInteraction(ui: TUI, host: Container, controller: AbortController): AuthInteraction {
  const messages = new Container(), prompts = new Container();
  host.clear(); host.addChild(messages); host.addChild(prompts);
  const notice = (text: string) => { messages.addChild(new Text(sanitizeTerminalText(text), 1, 0)); ui.requestRender(); };
  return {
    signal: controller.signal,
    notify(event) {
      if (controller.signal.aborted) return;
      if (event.type === "auth_url") notice(`请在浏览器打开：\n${event.url}\n${event.instructions ?? ""}`);
      else if (event.type === "device_code") notice(`请在浏览器打开：${event.verificationUri}\n输入设备码：${event.userCode}`);
      else {
        notice(event.message);
        if (event.type === "info") for (const link of event.links ?? []) notice(`${link.label ?? "说明"}: ${link.url}`);
      }
    },
    prompt(p) {
      const signal = p.signal ? AbortSignal.any([controller.signal, p.signal]) : controller.signal;
      signal.throwIfAborted();
      return new Promise<string>((resolve, reject) => {
        const input = p.type === "select" ? undefined : p.type === "secret" || p.type === "manual_code" ? new SecretInput() : new Input();
        const list = p.type === "select" ? new SelectList(p.options.map(o => ({
          value: o.id, label: sanitizeTerminalText(o.label), description: sanitizeTerminalText(o.description ?? ""),
        })), 9, selectListTheme()) : undefined;
        const cleanup = () => { signal.removeEventListener("abort", aborted); input?.setValue(""); prompts.clear(); ui.requestRender(); };
        const aborted = () => { cleanup(); reject(new DOMException("Cancelled", "AbortError")); };
        const finish = (value: string) => { cleanup(); resolve(value); };
        signal.addEventListener("abort", aborted, { once: true });
        prompts.clear();
        prompts.addChild(new Text(sanitizeTerminalText(p.message) + "（Esc 取消）", 1, 0));
        if (list) {
          list.onSelect = item => finish(item.value);
          list.onCancel = () => controller.abort();
          prompts.addChild(list); ui.setFocus(list);
        } else if (input) {
          input.onSubmit = value => finish(value);
          input.onEscape = () => controller.abort();
          prompts.addChild(input); ui.setFocus(input);
        }
        ui.requestRender();
      });
    },
  };
}
