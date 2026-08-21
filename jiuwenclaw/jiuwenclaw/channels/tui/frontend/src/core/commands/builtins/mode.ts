import type { AutocompleteItem } from "@mariozechner/pi-tui";
import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

/** TUI `/mode` 樹形展示；分組行 value 為 `agent`/`code`（與 modeAlias 預設一致），不修改 pi-tui。 */
export function buildModeAutocompleteItems(): AutocompleteItem[] {
  return [
    { value: "agent", label: "agent" },
    { value: "agent.plan", label: "    plan" },
    { value: "agent.fast", label: "    fast" },
    { value: "code", label: "code" },
    { value: "code.normal", label: "    normal" },
    { value: "code.plan", label: "    plan" },
    { value: "team", label: "team" },
  ];
}

export function createModeCommand(): SlashCommand {
  const directModes = [
    "agent",
    "code",
    "agent.plan",
    "agent.fast",
    "code.plan",
    "code.normal",
    "team",
  ] as const;
  /** 使用者輸入的簡寫 → 實際會話模式（/mode agent → agent.plan，/mode code → code.normal）。 */
  const modeAlias: Record<
    string,
    "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team"
  > = {
    plan: "agent.plan",
    agent: "agent.plan",
    code: "code.normal",
    "agent.plan": "agent.plan",
    "agent.fast": "agent.fast",
    "code.plan": "code.plan",
    "code.normal": "code.normal",
    team: "team",
  };

  return {
    name: "mode",
    description: "Switch chat mode",
    usage: "/mode <agent|code|team>",
    example: "/mode agent",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async () => [...directModes],
    action: async (ctx, args) => {
      const requestedMode = args.trim();
      // 無引數時顯示當前 mode
      if (!requestedMode) {
        const currentMode = ctx.mode ?? "unknown";
        ctx.addItem(
          makeItem(
            ctx.sessionId,
            "info",
            `Current mode: ${currentMode}`,
            "m",
          ),
        );
        return;
      }
      const nextMode = modeAlias[requestedMode];
      if (!nextMode) {
        ctx.addItem(
          makeItem(
            ctx.sessionId,
            "error",
            "usage: /mode <agent|code|team>",
          ),
        );
        return;
      }
      try {
        await ctx.request("mode.set", { mode: nextMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }
      ctx.setMode(nextMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode set to ${nextMode}`, "m"));
    },
  };
}
