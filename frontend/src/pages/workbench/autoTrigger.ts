/** 处理员自动触发的防重发表（模块级）。
 *
 *  键 = `${issue_id}:${step}`，值 = 本次逻辑触发持有的幂等键。放在模块级而不是
 *  组件 ref：组件重挂载（切会话再切回来）会重置 ref，重置后撞上读投影滞后就会
 *  对正在跑的步骤重复开火（409 的根源）。
 *
 *  消费方：WorkbenchPage 的驱动器（写入）；AssistantFlow 的失败重试（删除后让
 *  驱动器用新键重跑）。 */
const map = new Map<string, string>();

export const autoTrigger = {
  has: (key: string): boolean => map.has(key),
  get: (key: string): string | undefined => map.get(key),
  set: (key: string, value: string): void => {
    map.set(key, value);
  },
  /** 删除记录让驱动器可以重跑（新键——失败重试是一次新的逻辑触发） */
  delete: (key: string): void => {
    map.delete(key);
  },
};
