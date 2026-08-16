/** 添加仓库（批次 A-3/A-4）的数据源 + 会话存根。
 *  设计依据 `docs/development/full-loop-gui-design-20260812.md` ①。
 *
 *  这里没有 replay 分支：扫描是**写外部世界**的动作，回放夹具世界里没有对应事实，
 *  编一个假任务比不做更糟（同 workspaces.ts 的取舍）。replay 下由界面如实拒绝提交。 */
import type { ScanTaskView, UrlIdentification } from "./contract";
import { defaultClient } from "./client";

export function identifyUrl(url: string): Promise<UrlIdentification> {
  return defaultClient().identifyRepositoryUrl(url);
}

/** 请求体只送 `org_url`：`max_workers` 交给服务端缺省（1..20，缺省 5），
 *  多送字段会被 `extra="forbid"` 以 422 顶回来。 */
export function startOrgScan(orgUrl: string): Promise<ScanTaskView> {
  return defaultClient().scanOrganization({ org_url: orgUrl });
}

export function startRepoScan(repoUrl: string): Promise<ScanTaskView> {
  return defaultClient().scanRepository({ repo_url: repoUrl });
}

export function fetchScanTask(taskId: string): Promise<ScanTaskView> {
  return defaultClient().getScanTask(taskId);
}

/** 会话存根键。固定前缀、与工作区无关——存的是「这个浏览器标签页正在等哪次扫描」，
 *  不是业务数据。 */
const SCAN_TASK_KEY = "repomesh.console.scan-task";

/** 存根**只存 task_id**，不镜像任何计数。
 *
 *  计数与状态的唯一事实源是 `GET scan-tasks/{id}`：把它们抄进 sessionStorage 就有了
 *  第二份会过期的真相，恢复页面时先渲染一份旧数字再被真值覆盖，正是「用确定的旧结论
 *  冒充当前事实」。存 id、回来重新问一次，慢一个来回但不会说谎。 */
export function rememberScanTask(taskId: string): void {
  try {
    window.sessionStorage.setItem(SCAN_TASK_KEY, taskId);
  } catch {
    /* 隐私模式等存储不可用：功能降级为「离开页面即丢」，不影响本次轮询 */
  }
}

export function recallScanTask(): string | null {
  try {
    return window.sessionStorage.getItem(SCAN_TASK_KEY);
  } catch {
    return null;
  }
}

export function forgetScanTask(): void {
  try {
    window.sessionStorage.removeItem(SCAN_TASK_KEY);
  } catch {
    /* 同上 */
  }
}
