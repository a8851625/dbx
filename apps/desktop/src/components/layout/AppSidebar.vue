<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { translateBackendError } from "@/i18n/backend-errors";
import { Upload, Download, RefreshCw } from "lucide-vue-next";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipTrigger, TooltipContent } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import ConnectionTree from "@/components/sidebar/ConnectionTree.vue";
import { useConnectionStore } from "@/stores/connectionStore";
import { useToast } from "@/composables/useToast";

defineProps<{
  sidebarWidth: number;
  classicLayout?: boolean;
  canImportConfig?: boolean;
  canExportConfig?: boolean;
}>();

const emit = defineEmits<{
  import: [source: "dbx" | "navicat" | "dbeaver"];
  export: [];
  startResize: [event: MouseEvent];
}>();

const { t } = useI18n();
const connectionStore = useConnectionStore();
const { toast } = useToast();
const connectionTreeRef = ref<InstanceType<typeof ConnectionTree>>();

async function refreshTree() {
  try {
    await connectionStore.refreshAllTree();
  } catch (e: any) {
    toast(t("connection.connectFailed", { message: translateBackendError(t, e?.message || String(e)) }), 5000);
  }
}

function focusSearch(): boolean {
  return connectionTreeRef.value?.focusSearch() ?? false;
}

defineExpose({ focusSearch });
</script>

<template>
  <div
    class="h-full shrink-0 relative select-none"
    :class="classicLayout ? '' : 'rounded-md border border-border/80 bg-background'"
    :style="{ width: sidebarWidth + 'px' }"
  >
    <div class="h-full flex flex-col overflow-hidden">
      <div
        class="flex items-center px-3 text-xs font-medium text-muted-foreground border-b bg-muted/20"
        :class="classicLayout ? 'h-9' : 'h-10'"
      >
        {{ t("sidebar.connections") }}
        <span class="flex-1" />
        <Tooltip>
          <TooltipTrigger as-child>
            <Button variant="ghost" size="icon" class="h-5 w-5" @click="refreshTree">
              <RefreshCw class="h-3 w-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t("contextMenu.refreshChildren") }}</TooltipContent>
        </Tooltip>
        <DropdownMenu v-if="canImportConfig">
          <DropdownMenuTrigger as-child>
            <Button variant="ghost" size="icon" class="h-5 w-5" :title="t('sidebar.import')">
              <Upload class="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" class="w-44">
            <DropdownMenuItem @select.prevent="emit('import', 'dbx')">
              {{ t("sidebar.importDbx") }}
            </DropdownMenuItem>
            <DropdownMenuItem @select.prevent="emit('import', 'navicat')">
              {{ t("sidebar.importNavicat") }}
            </DropdownMenuItem>
            <DropdownMenuItem @select.prevent="emit('import', 'dbeaver')">
              {{ t("sidebar.importDbeaver") }}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Tooltip v-if="canExportConfig">
          <TooltipTrigger as-child>
            <Button variant="ghost" size="icon" class="h-5 w-5" @click="emit('export')">
              <Download class="h-3 w-3" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{{ t("sidebar.export") }}</TooltipContent>
        </Tooltip>
      </div>
      <div class="flex-1 min-h-0">
        <ConnectionTree ref="connectionTreeRef" />
      </div>
    </div>
    <div class="panel-resize-handle panel-resize-handle--right" @mousedown="emit('startResize', $event)" />
  </div>
</template>
