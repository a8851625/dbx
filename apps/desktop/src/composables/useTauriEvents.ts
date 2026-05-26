import { useConnectionStore } from "@/stores/connectionStore";
import { useQueryStore } from "@/stores/queryStore";
import type { NavigationTarget } from "@/composables/useNavigationTargets";

export function useTauriEvents(deps: {
  openTableTarget: (target: NavigationTarget) => Promise<void>;
  openSqlFilePath: (path: string) => Promise<void>;
  openConnectionDeepLink: (url: string) => Promise<void>;
}) {
  const connectionStore = useConnectionStore();
  const queryStore = useQueryStore();
  void deps;
  void connectionStore;
  void queryStore;

  function setupTauriListeners() {}

  function cleanupTauriListeners() {}

  return { setupTauriListeners, cleanupTauriListeners };
}
