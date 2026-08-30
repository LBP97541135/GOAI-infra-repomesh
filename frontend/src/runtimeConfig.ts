interface RepoMeshRuntimeConfig {
  apiToken?: string;
}

function runtimeConfig(): RepoMeshRuntimeConfig {
  return (
    window as typeof window & { __REPOMESH_CONFIG__?: RepoMeshRuntimeConfig }
  ).__REPOMESH_CONFIG__ ?? {};
}

export function browserApiToken(): string {
  return runtimeConfig().apiToken ?? import.meta.env.VITE_API_TOKEN ?? "";
}
