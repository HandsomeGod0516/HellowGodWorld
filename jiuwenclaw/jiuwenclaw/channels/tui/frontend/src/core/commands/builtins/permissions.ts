import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

const VALID_LEVELS = new Set(["allow", "ask", "deny"]);

export function createPermissionsCommand(): SlashCommand {
  return {
    name: "permissions",
    description: "Set per-tool guardrail level in permissions.tools (writes config)",
    usage: "/permissions <allow|ask|deny> <tool_name>",
    example: "/permissions ask write_file",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const raw = args.trim();
      if (!raw) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            "用法：/permissions <allow|ask|deny> <tool_name>  例：/permissions ask write_file",
          ),
        );
        return;
      }

      const parts = raw.split(/\s+/).filter(Boolean);
      if (parts.length < 2) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            "缺少引數。用法：/permissions <allow|ask|deny> <tool_name>",
          ),
        );
        return;
      }

      const level = parts[0].toLowerCase();
      const tool = parts.slice(1).join(" ").trim();
      if (!VALID_LEVELS.has(level)) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            `無效級別 “${parts[0]}”，僅允許：allow、ask、deny`,
          ),
        );
        return;
      }
      if (!tool) {
        ctx.addItem(addError(ctx.sessionId, "工具名不能為空。"));
        return;
      }

      try {
        await ctx.request<Record<string, unknown>>(
          "permissions.tools.update",
          { tool, level },
          60_000,
        );
        ctx.addItem(
          addInfo(ctx.sessionId, `已設定 permissions.tools.${tool} = ${level}`, "i"),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `permissions.tools.update 失敗：${message}`));
      }
    },
  };
}
