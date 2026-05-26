import { ref, onMounted, onUnmounted } from "vue";
import { isMacOS } from "@/lib/platform";

export function shouldReserveMacTrafficLightInset(isMac: boolean, isFullscreen: boolean): boolean {
  return isMac && !isFullscreen;
}

export function useWindowControls() {
  const isMaximized = ref(false);
  const isFullscreen = ref(false);
  const isMac = isMacOS();
  const isDesktop = false;
  const showControls = false;

  async function minimize() {}

  async function toggleMaximize() {}

  async function close() {}

  onMounted(() => {});

  onUnmounted(() => {});

  return {
    isMac,
    isDesktop,
    showControls,
    isMaximized,
    isFullscreen,
    minimize,
    toggleMaximize,
    close,
  };
}
