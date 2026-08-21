/**
 * 將 SkillNet / GitHub 技能目錄 URL 規範化為可比較形式（主機小寫、去尾斜槓等）。
 * 用於搜尋結果 skill_url 與本地 skills[].origin 對照。
 */
export function normalizeSkillNetUrl(raw: string): string {
  const s = raw.trim();
  if (!s) return "";
  try {
    const u = new URL(s.startsWith("http://") || s.startsWith("https://") ? s : `https://${s}`);
    if (u.hostname.toLowerCase() === "github.com") {
      u.protocol = "https:";
    }
    u.hostname = u.hostname.toLowerCase();
    let path = u.pathname;
    if (path.length > 1 && path.endsWith("/")) {
      path = path.slice(0, -1);
    }
    return `${u.origin}${path}${u.search}${u.hash}`;
  } catch {
    return s.replace(/\/$/, "").toLowerCase();
  }
}
