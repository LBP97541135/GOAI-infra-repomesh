/** 界面主题（浅色 / 深色）的唯一归属。
 *
 *  实现是「换令牌不换类名」：index.css 里所有工具类引用的都是 var(--color-*)，
 *  在 <html> 上放 data-theme="light"，浅色整套覆盖即生效（见 index.css 的
 *  html[data-theme="light"] 块）。这里只管三件事：读偏好、写偏好、把属性放到
 *  <html> 上。偏好存 localStorage——主题是「这台机器这个人」的观感选择，不是
 *  平台数据，不该进库也不该跟着账号走。
 *
 *  深色是产品默认（设计选型 Variant D 的原色），未存过偏好时 readStoredTheme
 *  返回 dark；把显式存的 "dark" 与「没存过」区分开没有消费者，故不区分。 */

export type ThemeName = "dark" | "light";

const STORAGE_KEY = "repomesh-theme";

export function readStoredTheme(): ThemeName {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    // localStorage 不可用（隐私模式等）时退化成默认深色，会话内切换仍有效
    return "dark";
  }
}

export function applyTheme(theme: ThemeName): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // 同上：存不进去就只当会话内偏好
  }
}
