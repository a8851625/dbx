import { useI18n } from "vue-i18n";
import { useToast } from "@/composables/useToast";

export function useFileDrop() {
  useI18n();
  useToast();

  async function setupFileDrop() {
    return;
  }

  return { setupFileDrop };
}
