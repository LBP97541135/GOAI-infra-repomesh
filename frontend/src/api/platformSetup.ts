/** 平台就绪与 Coding Agent 探测（`/api/v1/setup/*`，main 装机向导的读面）。
 *
 *  这两个端点**无鉴权**（后端 `platform_setup.py` 只有 onboard 那条要管理员），
 *  故走 `api/client.ts` 的通道即可——带上动作 token 无害，端点不看它。
 *
 *  设置页此前对适配器清单写的是「没有一项有数据源」，那句话在这两个端点合进来之前
 *  是对的。现在**读**的那半有源了：装没装、认没认得上、能不能被已验证的驱动跑。
 *  仍然无源的是**写**（适配器注册表配置入口，二期）和 CLI 版本号（Controller 不
 *  回报），设置页的缺口清单据此收窄而不是清空。 */
import type {
  CodingAgentsProbe,
  SetupStatusView,
} from "./contract";
import { defaultClient } from "./client";

export type {
  AdapterAuthStatus,
  CodingAgentAdapterView,
  CodingAgentsProbe,
  SetupStatusView,
} from "./contract";

export function fetchSetupStatus(): Promise<SetupStatusView> {
  return defaultClient().getSetupStatus();
}

export function fetchCodingAgents(): Promise<CodingAgentsProbe> {
  return defaultClient().getCodingAgents();
}
