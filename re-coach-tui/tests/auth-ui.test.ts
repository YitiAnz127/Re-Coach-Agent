import { describe, expect, it, vi } from "vitest";
import { Container, type TUI } from "@earendil-works/pi-tui";
import { createAuthInteraction, SecretInput } from "../src/ui/auth.js";
import { modelCommandError } from "../src/model-runtime.js";

describe("auth UI boundaries", () => {
  it("never renders a secret value even when focused or narrow", () => {
    const input = new SecretInput(); input.setValue("secret-api-key"); input.focused = true;
    expect(input.render(80).join("")).not.toContain("secret-api-key");
    expect(input.render(80).join("")).toContain("•");
    expect(input.render(3).join("")).not.toContain("secret");
  });
  it("cancels a pending auth prompt and removes its input", async () => {
    const host = new Container();
    const ui = { setFocus: vi.fn(), requestRender: vi.fn() } as unknown as TUI;
    const controller = new AbortController();
    const interaction = createAuthInteraction(ui, host, controller);
    const promise = interaction.prompt({ type: "secret", message: "API key" });
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: "AbortError" });
  });
  it("hides upstream login error text", () => {
    expect(modelCommandError(new Error("key-or-token-must-not-escape"))).not.toContain("key-or-token");
  });
});
