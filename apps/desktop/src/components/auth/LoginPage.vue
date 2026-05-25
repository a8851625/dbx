<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { Button } from "@/components/ui/button";
import { Loader2, ShieldCheck } from "lucide-vue-next";

const props = withDefaults(
  defineProps<{
    setupMode?: boolean;
    providerName?: string | null;
    loginUrl?: string | null;
    mockMode?: boolean;
  }>(),
  { setupMode: false, providerName: null, loginUrl: null, mockMode: false },
);

const emit = defineEmits<{ authenticated: [] }>();
const { t } = useI18n();

const error = ref("");
const loading = ref(false);

async function submit() {
  loading.value = true;
  error.value = "";
  try {
    const url = props.loginUrl ?? "/api/v1/auth/login";
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.authorization_url) {
        window.location.assign(data.authorization_url);
        return;
      }
      emit("authenticated");
    } else {
      const text = await res.text();
      error.value = text || t("auth.loginFailed");
    }
  } catch (e: any) {
    error.value = e?.message || t("auth.connectFailed");
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div
    class="flex items-center justify-center h-screen bg-gradient-to-br from-background via-background to-blue-950/20"
  >
    <div class="w-[360px] space-y-8">
      <div class="flex flex-col items-center gap-4">
        <img src="/logo.png" alt="DBX" class="w-20 h-20 rounded-2xl shadow-lg shadow-blue-500/20" />
        <div class="text-center">
          <h1 class="text-2xl font-bold tracking-tight">DBX</h1>
          <p class="text-sm text-muted-foreground mt-1">
            {{ setupMode ? t("auth.setupDescription") : t("auth.oidcLoginDescription") }}
          </p>
        </div>
      </div>

      <form class="space-y-4" @submit.prevent="submit" autocomplete="off">
        <div v-if="setupMode" class="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <ShieldCheck class="w-4 h-4" />
          <span>{{ t("auth.setupTitle") }}</span>
        </div>
        <div class="rounded-xl border border-border/80 bg-card/60 p-4 text-sm text-muted-foreground">
          <p class="font-medium text-foreground">{{ props.providerName || t("auth.defaultProviderName") }}</p>
          <p class="mt-2">{{ t("auth.oidcLoginHint") }}</p>
          <p v-if="mockMode" class="mt-2 text-xs text-amber-500">{{ t("auth.mockModeHint") }}</p>
        </div>
        <p v-if="error" class="text-sm text-destructive text-center">{{ error }}</p>
        <Button type="submit" class="w-full h-11 text-sm font-medium" :disabled="loading">
          <Loader2 v-if="loading" class="w-4 h-4 animate-spin mr-2" />
          {{
            loading
              ? t("auth.processing")
              : setupMode
                ? t("auth.setPassword")
                : t("auth.loginWithProvider", { provider: props.providerName || t("auth.defaultProviderName") })
          }}
        </Button>
      </form>

      <p class="text-center text-xs text-muted-foreground/50">Powered by DBX</p>
    </div>
  </div>
</template>
