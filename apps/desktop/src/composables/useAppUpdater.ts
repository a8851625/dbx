import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import * as api from "@/lib/api";

export function shouldOpenUpdateDialog(options: { silent?: boolean }) {
  return options.silent !== true;
}

export function useAppUpdater() {
  const { t } = useI18n();

  const checkingUpdates = ref(false);
  const updateInfo = ref<api.UpdateInfo | null>(null);
  const updateCheckMessage = ref("");
  const showUpdateDialog = ref(false);
  const isDownloadingUpdate = ref(false);
  const downloadProgress = ref(0);
  const updateReady = ref(false);
  const hasUpdateAvailable = computed(() => updateInfo.value?.update_available === true);
  const latestReleaseUrl = "https://github.com/t8y2/dbx/releases/latest";

  function openUrl(url: string) {
    window.open(url, "_blank", "noopener,noreferrer");
  }

  async function checkUpdates(options: { silent?: boolean } = {}) {
    if (checkingUpdates.value) return;
    checkingUpdates.value = true;
    updateCheckMessage.value = "";
    try {
      const info = await api.checkForUpdates();
      updateInfo.value = info;
      if (info.update_available) {
        if (shouldOpenUpdateDialog({ silent: options.silent })) {
          showUpdateDialog.value = true;
        }
      } else if (!options.silent) {
        updateCheckMessage.value = t("updates.upToDate", { version: info.current_version });
        showUpdateDialog.value = true;
      }
    } catch (e: any) {
      if (!options.silent) {
        updateCheckMessage.value = formatUpdateError(String(e));
        showUpdateDialog.value = true;
      }
    } finally {
      checkingUpdates.value = false;
    }
  }

  function formatUpdateError(message: string): string {
    const lower = message.toLowerCase();
    if (lower.includes("403") || lower.includes("rate limit")) {
      return t("updates.rateLimited");
    }
    return t("updates.failed", { error: message });
  }

  function openLatestRelease() {
    const url = updateInfo.value?.release_url || latestReleaseUrl;
    openUrl(url);
  }

  async function downloadAndInstallUpdate() {
    if (isDownloadingUpdate.value) return;
    openLatestRelease();
  }

  async function restartApp() {
    openLatestRelease();
  }

  return {
    checkingUpdates,
    updateInfo,
    updateCheckMessage,
    showUpdateDialog,
    isDownloadingUpdate,
    downloadProgress,
    updateReady,
    hasUpdateAvailable,
    latestReleaseUrl,
    openUrl,
    checkUpdates,
    formatUpdateError,
    openLatestRelease,
    downloadAndInstallUpdate,
    restartApp,
  };
}
