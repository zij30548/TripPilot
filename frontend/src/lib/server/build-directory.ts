/** Trusted local build/start setting, never a browser or upstream parameter.
 * Separate artifacts let controlled acceptance leave a running .next intact.
 */
export function buildDirectory(value: string | undefined): string {
  if (value === undefined || value === ".next") return ".next";
  if (!/^\.next-[a-z0-9][a-z0-9-]{0,47}$/.test(value)) {
    throw new Error("TRIPPILOT_NEXT_DIST_DIR 只能使用 .next 或项目内 .next- 开头的安全构建目录名。");
  }
  return value;
}
