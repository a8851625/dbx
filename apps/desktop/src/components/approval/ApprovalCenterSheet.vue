<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { CircleAlert, Loader2, RefreshCw, RotateCcw, Send, ShieldCheck } from "lucide-vue-next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/composables/useToast";
import { translateBackendError } from "@/i18n/backend-errors";
import * as api from "@/lib/api";
import type { ApprovalFlowRecord, ApprovalTicketRecord } from "@/lib/api";
import type { ConnectionConfig } from "@/types/database";

type TicketScope = "my" | "pending" | "all";
type ApprovalView = "create" | TicketScope;

const props = withDefaults(
  defineProps<{
    open: boolean;
    connections: ConnectionConfig[];
    draftConnectionId?: string;
    draftDatabase?: string;
    draftSchema?: string;
    draftSql?: string;
    draftTitle?: string;
    canCreate?: boolean;
    canViewAll?: boolean;
  }>(),
  {
    canCreate: false,
    canViewAll: false,
    draftConnectionId: "",
    draftDatabase: "",
    draftSchema: "",
    draftSql: "",
    draftTitle: "",
  },
);

const emit = defineEmits<{
  "update:open": [value: boolean];
}>();

const { t } = useI18n();
const { toast } = useToast();

const loading = ref(false);
const actionLoading = ref(false);
const flows = ref<ApprovalFlowRecord[]>([]);
const myTickets = ref<ApprovalTicketRecord[]>([]);
const pendingTickets = ref<ApprovalTicketRecord[]>([]);
const allTickets = ref<ApprovalTicketRecord[]>([]);
const activeView = ref<ApprovalView>(props.canCreate ? "create" : "my");
const selectedTicketId = ref("");

const decisionOpen = ref(false);
const decisionMode = ref<"approve" | "reject">("approve");
const decisionTicketId = ref("");
const decisionComment = ref("");

const form = reactive({
  title: "",
  datasourceId: "",
  targetDatabase: "",
  targetSchema: "",
  targetTable: "",
  sqlText: "",
  scheduledAt: "",
});

const canCreate = computed(() => props.canCreate);
const canViewAll = computed(() => props.canViewAll);
const connections = computed(() => props.connections);
const ticketScopes = computed<TicketScope[]>(() => (props.canViewAll ? ["my", "pending", "all"] : ["my", "pending"]));

const selectedTicket = computed(() => {
  if (!isTicketScope(activeView.value)) {
    return null;
  }
  const tickets = ticketsForScope(activeView.value);
  return tickets.find((ticket) => ticket.id === selectedTicketId.value) ?? tickets[0] ?? null;
});

const canCreateTicket = computed(
  () => !!form.title.trim() && !!form.datasourceId && !!form.targetDatabase.trim() && !!form.sqlText.trim(),
);

const modelOpen = computed({
  get: () => props.open,
  set: (value: boolean) => emit("update:open", value),
});

watch(
  () => props.open,
  (open) => {
    if (!open) {
      decisionOpen.value = false;
      return;
    }
    resetForm();
    activeView.value = props.canCreate ? "create" : "my";
    void reloadAll({ resetSelection: true });
  },
);

watch(activeView, (view) => {
  if (!isTicketScope(view)) {
    return;
  }
  const tickets = ticketsForScope(view);
  if (!tickets.some((ticket) => ticket.id === selectedTicketId.value)) {
    selectedTicketId.value = tickets[0]?.id ?? "";
  }
});

function isTicketScope(value: ApprovalView): value is TicketScope {
  return value === "my" || value === "pending" || value === "all";
}

function resetForm() {
  form.title = props.draftTitle?.trim() || t("approval.defaultTitle");
  form.datasourceId = props.draftConnectionId || props.connections[0]?.id || "";
  form.targetDatabase = props.draftDatabase || "";
  form.targetSchema = props.draftSchema || "";
  form.targetTable = "";
  form.sqlText = props.draftSql || "";
  form.scheduledAt = "";
}

function ticketsForScope(scope: TicketScope): ApprovalTicketRecord[] {
  if (scope === "pending") {
    return pendingTickets.value;
  }
  if (scope === "all") {
    return allTickets.value;
  }
  return myTickets.value;
}

function syncSelectedTicket(resetSelection: boolean) {
  const preferredId = resetSelection ? "" : selectedTicketId.value;
  const activeScope = isTicketScope(activeView.value) ? activeView.value : "my";
  const activeTickets = ticketsForScope(activeScope);
  if (preferredId && activeTickets.some((ticket) => ticket.id === preferredId)) {
    selectedTicketId.value = preferredId;
    return;
  }
  if (activeTickets.length > 0) {
    selectedTicketId.value = activeTickets[0].id;
    return;
  }
  for (const scope of ticketScopes.value) {
    const tickets = ticketsForScope(scope);
    if (tickets.length > 0) {
      selectedTicketId.value = tickets[0].id;
      if (!isTicketScope(activeView.value)) {
        activeView.value = scope;
      }
      return;
    }
  }
  selectedTicketId.value = "";
}

async function reloadAll(options: { resetSelection?: boolean } = {}) {
  loading.value = true;
  try {
    const [flowData, my, pending, all] = await Promise.all([
      api.listApprovalFlows(),
      api.listApprovalTickets("my"),
      api.listApprovalTickets("pending"),
      props.canViewAll ? api.listApprovalTickets("all") : Promise.resolve([]),
    ]);
    flows.value = flowData;
    myTickets.value = my;
    pendingTickets.value = pending;
    allTickets.value = all;
    syncSelectedTicket(Boolean(options.resetSelection));
  } catch (error: any) {
    toast(
      t("approval.loadFailed", {
        message: translateBackendError(t, error?.message || String(error)),
      }),
      5000,
    );
  } finally {
    loading.value = false;
  }
}

function normalizeOptionalText(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function normalizeSchedule(value: string): string | null {
  return value ? new Date(value).toISOString() : null;
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return t("approval.notScheduled");
  }
  return new Date(value).toLocaleString();
}

function statusVariant(status: string) {
  if (["failed", "rejected", "cancelled"].includes(status)) {
    return "destructive" as const;
  }
  if (["approved", "succeeded"].includes(status)) {
    return "default" as const;
  }
  if (["pending_approval", "queued", "executing", "running"].includes(status)) {
    return "secondary" as const;
  }
  return "outline" as const;
}

function riskVariant(risk: string) {
  if (risk === "high") {
    return "destructive" as const;
  }
  if (risk === "medium") {
    return "secondary" as const;
  }
  return "outline" as const;
}

async function createTicket(submitAfterCreate: boolean) {
  if (!canCreateTicket.value) {
    return;
  }
  actionLoading.value = true;
  try {
    let ticket = await api.createApprovalTicket({
      title: form.title.trim(),
      datasource_id: form.datasourceId,
      target_database: form.targetDatabase.trim(),
      target_schema: normalizeOptionalText(form.targetSchema),
      target_table: normalizeOptionalText(form.targetTable),
      sql_text: form.sqlText.trim(),
      scheduled_at: normalizeSchedule(form.scheduledAt),
    });
    if (submitAfterCreate) {
      ticket = await api.submitApprovalTicket(ticket.id);
    }
    activeView.value = "my";
    selectedTicketId.value = ticket.id;
    await reloadAll();
    toast(t(submitAfterCreate ? "approval.createdAndSubmitted" : "approval.createdDraft"), 2500);
  } catch (error: any) {
    toast(
      t("approval.actionFailed", {
        message: translateBackendError(t, error?.message || String(error)),
      }),
      5000,
    );
  } finally {
    actionLoading.value = false;
  }
}

async function submitTicket(ticketId: string) {
  actionLoading.value = true;
  try {
    const ticket = await api.submitApprovalTicket(ticketId);
    selectedTicketId.value = ticket.id;
    await reloadAll();
    toast(t("approval.submitted"), 2500);
  } catch (error: any) {
    toast(
      t("approval.actionFailed", {
        message: translateBackendError(t, error?.message || String(error)),
      }),
      5000,
    );
  } finally {
    actionLoading.value = false;
  }
}

function openDecision(ticketId: string, mode: "approve" | "reject") {
  decisionMode.value = mode;
  decisionTicketId.value = ticketId;
  decisionComment.value = "";
  decisionOpen.value = true;
}

async function confirmDecision() {
  if (!decisionTicketId.value) {
    return;
  }
  actionLoading.value = true;
  try {
    const comment = decisionComment.value.trim() || undefined;
    const ticket =
      decisionMode.value === "approve"
        ? await api.approveApprovalTicket(decisionTicketId.value, comment)
        : await api.rejectApprovalTicket(decisionTicketId.value, comment);
    decisionOpen.value = false;
    selectedTicketId.value = ticket.id;
    await reloadAll();
    toast(t(decisionMode.value === "approve" ? "approval.approved" : "approval.rejected"), 2500);
  } catch (error: any) {
    toast(
      t("approval.actionFailed", {
        message: translateBackendError(t, error?.message || String(error)),
      }),
      5000,
    );
  } finally {
    actionLoading.value = false;
  }
}

async function retryTicket(ticketId: string) {
  actionLoading.value = true;
  try {
    const ticket = await api.retryApprovalTicket(ticketId);
    selectedTicketId.value = ticket.id;
    await reloadAll();
    toast(t("approval.retried"), 2500);
  } catch (error: any) {
    toast(
      t("approval.actionFailed", {
        message: translateBackendError(t, error?.message || String(error)),
      }),
      5000,
    );
  } finally {
    actionLoading.value = false;
  }
}
</script>

<template>
  <Sheet :open="modelOpen" @update:open="modelOpen = $event">
    <SheetContent side="right" class="w-[min(1180px,96vw)] max-w-none gap-0 p-0">
      <SheetHeader class="border-b px-6 py-5 pr-14">
        <SheetTitle class="flex items-center gap-2 text-base">
          <ShieldCheck class="h-4 w-4" />
          {{ t("approval.title") }}
        </SheetTitle>
        <p class="text-sm text-muted-foreground">{{ t("approval.description") }}</p>
      </SheetHeader>

      <div class="flex min-h-0 flex-1 flex-col">
        <Tabs v-model:model-value="activeView" class="flex min-h-0 flex-1">
          <div class="border-b px-6 py-3">
            <TabsList variant="line" class="w-auto gap-1">
              <TabsTrigger v-if="canCreate" value="create">
                {{ t("approval.createTab") }}
              </TabsTrigger>
              <TabsTrigger value="my">{{ t("approval.myTicketsTab") }}</TabsTrigger>
              <TabsTrigger value="pending">{{ t("approval.pendingTab") }}</TabsTrigger>
              <TabsTrigger v-if="canViewAll" value="all">{{ t("approval.allTab") }}</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent v-if="canCreate" value="create" class="min-h-0">
            <div class="grid h-full min-h-0 gap-0 lg:grid-cols-[minmax(0,1.1fr)_360px]">
              <ScrollArea class="min-h-0 border-r">
                <div class="space-y-5 px-6 py-5">
                  <div>
                    <h3 class="text-sm font-semibold">{{ t("approval.newTicketTitle") }}</h3>
                    <p class="mt-1 text-sm text-muted-foreground">{{ t("approval.newTicketDescription") }}</p>
                  </div>

                  <div class="grid gap-4 md:grid-cols-2">
                    <div class="space-y-2 md:col-span-2">
                      <Label for="approval-ticket-title">{{ t("approval.ticketTitle") }}</Label>
                      <Input id="approval-ticket-title" v-model="form.title" />
                    </div>

                    <div class="space-y-2">
                      <Label>{{ t("approval.datasource") }}</Label>
                      <Select v-model="form.datasourceId">
                        <SelectTrigger class="w-full">
                          <SelectValue :placeholder="t('approval.datasourcePlaceholder')" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem v-for="connection in connections" :key="connection.id" :value="connection.id">
                            {{ connection.name }}
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <div class="space-y-2">
                      <Label for="approval-ticket-database">{{ t("approval.database") }}</Label>
                      <Input id="approval-ticket-database" v-model="form.targetDatabase" />
                    </div>

                    <div class="space-y-2">
                      <Label for="approval-ticket-schema">{{ t("approval.schema") }}</Label>
                      <Input id="approval-ticket-schema" v-model="form.targetSchema" :placeholder="t('approval.optionalField')" />
                    </div>

                    <div class="space-y-2">
                      <Label for="approval-ticket-table">{{ t("approval.table") }}</Label>
                      <Input id="approval-ticket-table" v-model="form.targetTable" :placeholder="t('approval.optionalField')" />
                    </div>

                    <div class="space-y-2 md:col-span-2">
                      <div class="flex items-center justify-between gap-3">
                        <Label for="approval-ticket-schedule">{{ t("approval.schedule") }}</Label>
                        <Button variant="ghost" size="sm" class="h-7 px-2 text-xs" @click="form.scheduledAt = ''">
                          {{ t("approval.clearSchedule") }}
                        </Button>
                      </div>
                      <Input
                        id="approval-ticket-schedule"
                        v-model="form.scheduledAt"
                        type="datetime-local"
                        :placeholder="t('approval.schedulePlaceholder')"
                      />
                      <p class="text-xs text-muted-foreground">{{ t("approval.scheduleHint") }}</p>
                    </div>

                    <div class="space-y-2 md:col-span-2">
                      <Label for="approval-ticket-sql">{{ t("approval.sql") }}</Label>
                      <textarea
                        id="approval-ticket-sql"
                        v-model="form.sqlText"
                        class="min-h-[280px] w-full rounded-md border bg-background px-3 py-2 font-mono text-sm outline-none transition focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
                        spellcheck="false"
                      />
                    </div>
                  </div>

                  <div class="flex flex-wrap items-center gap-2">
                    <Button :disabled="actionLoading || !canCreateTicket" @click="createTicket(false)">
                      <Loader2 v-if="actionLoading" class="mr-2 h-4 w-4 animate-spin" />
                      {{ t("approval.createDraft") }}
                    </Button>
                    <Button variant="secondary" :disabled="actionLoading || !canCreateTicket" @click="createTicket(true)">
                      <Send class="mr-2 h-4 w-4" />
                      {{ t("approval.createAndSubmit") }}
                    </Button>
                  </div>
                </div>
              </ScrollArea>

              <ScrollArea class="min-h-0">
                <div class="space-y-4 px-6 py-5">
                  <div class="rounded-lg border bg-muted/20 p-4">
                    <div class="flex items-center gap-2 text-sm font-semibold">
                      <ShieldCheck class="h-4 w-4" />
                      {{ t("approval.flowTemplate") }}
                    </div>
                    <p class="mt-1 text-sm text-muted-foreground">{{ t("approval.flowTemplateHint") }}</p>
                  </div>

                  <div v-if="loading" class="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 class="h-4 w-4 animate-spin" />
                    {{ t("approval.loading") }}
                  </div>

                  <template v-else-if="flows.length > 0">
                    <div v-for="flow in flows" :key="flow.id" class="rounded-lg border p-4">
                      <div class="flex flex-wrap items-center gap-2">
                        <h4 class="text-sm font-semibold">{{ flow.name }}</h4>
                        <Badge variant="outline">{{ flow.ticket_type }}</Badge>
                        <Badge :variant="flow.enabled ? 'default' : 'outline'">
                          {{ flow.enabled ? t("approval.enabled") : t("approval.disabled") }}
                        </Badge>
                      </div>
                      <p v-if="flow.description" class="mt-2 text-sm text-muted-foreground">{{ flow.description }}</p>
                      <div class="mt-3 space-y-2">
                        <div v-for="step in flow.steps" :key="step.id" class="rounded-md bg-muted/40 px-3 py-2 text-sm">
                          <div class="flex items-center gap-2">
                            <Badge variant="secondary">#{{ step.step_no }}</Badge>
                            <span class="font-medium">{{ step.step_name }}</span>
                          </div>
                          <div class="mt-1 text-xs text-muted-foreground">
                            {{ t("approval.stepRule", { approver: step.approver_ref, mode: step.approval_mode }) }}
                          </div>
                        </div>
                      </div>
                    </div>
                  </template>

                  <div v-else class="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">
                    {{ t("approval.noTemplates") }}
                  </div>
                </div>
              </ScrollArea>
            </div>
          </TabsContent>

          <TabsContent v-for="scope in ticketScopes" :key="scope" :value="scope" class="min-h-0">
            <div class="grid h-full min-h-0 gap-0 lg:grid-cols-[340px_minmax(0,1fr)]">
              <div class="flex min-h-0 flex-col border-r">
                <div class="flex items-center gap-2 border-b px-4 py-3">
                  <p class="text-xs text-muted-foreground">
                    {{
                      scope === "my"
                        ? t("approval.scopeMyDescription")
                        : scope === "pending"
                          ? t("approval.scopePendingDescription")
                          : t("approval.scopeAllDescription")
                    }}
                  </p>
                  <span class="flex-1" />
                  <Button variant="ghost" size="icon-sm" :disabled="loading" @click="reloadAll()">
                    <RefreshCw class="h-4 w-4" :class="{ 'animate-spin': loading }" />
                  </Button>
                </div>

                <ScrollArea class="min-h-0 flex-1">
                  <div v-if="ticketsForScope(scope).length === 0" class="px-4 py-8 text-sm text-muted-foreground">
                    {{ t("approval.emptyScope") }}
                  </div>
                  <button
                    v-for="ticket in ticketsForScope(scope)"
                    :key="ticket.id"
                    class="flex w-full flex-col gap-2 border-b px-4 py-3 text-left transition hover:bg-muted/40"
                    :class="{ 'bg-muted/50': ticket.id === selectedTicket?.id }"
                    @click="selectedTicketId = ticket.id"
                  >
                    <div class="flex items-start justify-between gap-3">
                      <div class="min-w-0">
                        <div class="truncate text-sm font-medium">{{ ticket.title }}</div>
                        <div class="mt-1 truncate text-xs text-muted-foreground">{{ ticket.ticket_no }}</div>
                      </div>
                      <Badge :variant="statusVariant(ticket.current_status)">
                        {{ t(`approval.status.${ticket.current_status}`) }}
                      </Badge>
                    </div>
                    <div class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <Badge :variant="riskVariant(ticket.risk_level)">{{ t(`approval.risk.${ticket.risk_level}`) }}</Badge>
                      <span>{{ ticket.datasource_id }}</span>
                      <span>{{ ticket.target_database }}</span>
                    </div>
                  </button>
                </ScrollArea>
              </div>

              <div class="flex min-h-0 flex-col">
                <div
                  v-if="selectedTicket"
                  class="flex flex-wrap items-center gap-2 border-b px-5 py-4"
                >
                  <div class="min-w-0 flex-1">
                    <div class="truncate text-base font-semibold">{{ selectedTicket.title }}</div>
                    <div class="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span>{{ selectedTicket.ticket_no }}</span>
                      <span>{{ selectedTicket.datasource_id }}</span>
                      <span>{{ selectedTicket.target_database }}</span>
                      <span v-if="selectedTicket.target_schema">{{ selectedTicket.target_schema }}</span>
                      <span v-if="selectedTicket.target_table">{{ selectedTicket.target_table }}</span>
                    </div>
                  </div>
                  <Badge :variant="statusVariant(selectedTicket.current_status)">
                    {{ t(`approval.status.${selectedTicket.current_status}`) }}
                  </Badge>
                  <Badge :variant="riskVariant(selectedTicket.risk_level)">
                    {{ t(`approval.risk.${selectedTicket.risk_level}`) }}
                  </Badge>
                  <Button
                    v-if="selectedTicket.available_actions.includes('submit')"
                    size="sm"
                    :disabled="actionLoading"
                    @click="submitTicket(selectedTicket.id)"
                  >
                    <Send class="mr-2 h-4 w-4" />
                    {{ t("approval.actions.submit") }}
                  </Button>
                  <Button
                    v-if="selectedTicket.available_actions.includes('approve')"
                    size="sm"
                    :disabled="actionLoading"
                    @click="openDecision(selectedTicket.id, 'approve')"
                  >
                    {{ t("approval.actions.approve") }}
                  </Button>
                  <Button
                    v-if="selectedTicket.available_actions.includes('reject')"
                    size="sm"
                    variant="outline"
                    :disabled="actionLoading"
                    @click="openDecision(selectedTicket.id, 'reject')"
                  >
                    {{ t("approval.actions.reject") }}
                  </Button>
                  <Button
                    v-if="selectedTicket.available_actions.includes('retry')"
                    size="sm"
                    variant="secondary"
                    :disabled="actionLoading"
                    @click="retryTicket(selectedTicket.id)"
                  >
                    <RotateCcw class="mr-2 h-4 w-4" />
                    {{ t("approval.actions.retry") }}
                  </Button>
                </div>

                <ScrollArea v-if="selectedTicket" class="min-h-0 flex-1">
                  <div class="space-y-5 px-5 py-4">
                    <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <div class="rounded-lg border bg-muted/20 p-3">
                        <div class="text-xs text-muted-foreground">{{ t("approval.createdAt") }}</div>
                        <div class="mt-1 text-sm font-medium">{{ formatDateTime(selectedTicket.created_at) }}</div>
                      </div>
                      <div class="rounded-lg border bg-muted/20 p-3">
                        <div class="text-xs text-muted-foreground">{{ t("approval.submittedAt") }}</div>
                        <div class="mt-1 text-sm font-medium">{{ formatDateTime(selectedTicket.submitted_at) }}</div>
                      </div>
                      <div class="rounded-lg border bg-muted/20 p-3">
                        <div class="text-xs text-muted-foreground">{{ t("approval.approvedAt") }}</div>
                        <div class="mt-1 text-sm font-medium">{{ formatDateTime(selectedTicket.approved_at) }}</div>
                      </div>
                      <div class="rounded-lg border bg-muted/20 p-3">
                        <div class="text-xs text-muted-foreground">{{ t("approval.executedAt") }}</div>
                        <div class="mt-1 text-sm font-medium">{{ formatDateTime(selectedTicket.executed_at) }}</div>
                      </div>
                    </div>

                    <div class="space-y-2">
                      <div class="flex items-center justify-between gap-3">
                        <h4 class="text-sm font-semibold">{{ t("approval.sql") }}</h4>
                        <div class="text-xs text-muted-foreground">
                          {{ t("approval.scheduleValue", { value: formatDateTime(selectedTicket.scheduled_at) }) }}
                        </div>
                      </div>
                      <pre class="overflow-x-auto rounded-lg border bg-muted/20 px-4 py-3 text-xs leading-6">{{
                        selectedTicket.sql_text
                      }}</pre>
                    </div>

                    <div class="space-y-3">
                      <h4 class="text-sm font-semibold">{{ t("approval.statements") }}</h4>
                      <div
                        v-for="statement in selectedTicket.statements"
                        :key="statement.id"
                        class="rounded-lg border px-4 py-3"
                      >
                        <div class="flex flex-wrap items-center gap-2">
                          <Badge variant="secondary">#{{ statement.statement_order }}</Badge>
                          <Badge variant="outline">{{ statement.statement_type.toUpperCase() }}</Badge>
                          <Badge :variant="riskVariant(statement.risk_level)">
                            {{ t(`approval.risk.${statement.risk_level}`) }}
                          </Badge>
                          <Badge
                            v-for="tag in statement.risk_tags"
                            :key="tag"
                            variant="outline"
                          >
                            {{ tag }}
                          </Badge>
                        </div>
                        <pre class="mt-3 overflow-x-auto rounded-md bg-muted/30 px-3 py-2 text-xs leading-6">{{
                          statement.statement_text
                        }}</pre>
                      </div>
                    </div>

                    <div class="space-y-3">
                      <h4 class="text-sm font-semibold">{{ t("approval.approvalFlow") }}</h4>
                      <div v-if="selectedTicket.approval_instance" class="rounded-lg border">
                        <div
                          v-for="step in selectedTicket.approval_instance.steps"
                          :key="step.id"
                          class="px-4 py-3"
                        >
                          <div class="flex flex-wrap items-center gap-2">
                            <Badge variant="secondary">#{{ step.step_no }}</Badge>
                            <span class="text-sm font-medium">{{ step.step_name }}</span>
                            <Badge :variant="statusVariant(step.status)">{{ t(`approval.status.${step.status}`) }}</Badge>
                          </div>
                          <div class="mt-1 text-xs text-muted-foreground">
                            {{ t("approval.stepRule", { approver: step.approver_ref, mode: step.approval_mode }) }}
                          </div>
                          <div v-if="step.actions.length > 0" class="mt-3 space-y-2">
                            <div
                              v-for="action in step.actions"
                              :key="action.id"
                              class="rounded-md bg-muted/30 px-3 py-2 text-xs"
                            >
                              <div class="flex items-center gap-2">
                                <Badge variant="outline">{{ action.action }}</Badge>
                                <span>{{ formatDateTime(action.created_at) }}</span>
                              </div>
                              <p v-if="action.comment" class="mt-1 text-muted-foreground">{{ action.comment }}</p>
                            </div>
                          </div>
                          <Separator
                            v-if="step !== selectedTicket.approval_instance.steps[selectedTicket.approval_instance.steps.length - 1]"
                            class="mt-3"
                          />
                        </div>
                      </div>
                      <div v-else class="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                        {{ t("approval.noApprovalFlow") }}
                      </div>
                    </div>

                    <div class="space-y-3">
                      <h4 class="text-sm font-semibold">{{ t("approval.executionJobs") }}</h4>
                      <div v-if="selectedTicket.execution_jobs.length === 0" class="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                        {{ t("approval.noExecutionJobs") }}
                      </div>
                      <div
                        v-for="job in selectedTicket.execution_jobs"
                        :key="job.id"
                        class="rounded-lg border px-4 py-3"
                      >
                        <div class="flex flex-wrap items-center gap-2">
                          <Badge :variant="statusVariant(job.status)">{{ t(`approval.status.${job.status}`) }}</Badge>
                          <Badge variant="outline">{{ job.execution_mode }}</Badge>
                          <span class="text-xs text-muted-foreground">{{ job.run_key }}</span>
                        </div>
                        <div class="mt-2 grid gap-2 text-xs text-muted-foreground md:grid-cols-3">
                          <div>{{ t("approval.startedAt") }}: {{ formatDateTime(job.started_at) }}</div>
                          <div>{{ t("approval.finishedAt") }}: {{ formatDateTime(job.finished_at) }}</div>
                          <div>{{ t("approval.executor") }}: {{ job.executor_type }}</div>
                        </div>
                        <p v-if="job.error_message" class="mt-2 text-sm text-destructive">{{ job.error_message }}</p>
                        <div v-if="job.statements.length > 0" class="mt-3 space-y-2">
                          <div
                            v-for="statement in job.statements"
                            :key="statement.id"
                            class="rounded-md bg-muted/30 px-3 py-2"
                          >
                            <div class="flex flex-wrap items-center gap-2 text-xs">
                              <Badge :variant="statement.success ? 'default' : 'destructive'">
                                {{ statement.success ? t("approval.statementSuccess") : t("approval.statementFailed") }}
                              </Badge>
                              <span>#{{ statement.statement_order }}</span>
                              <span v-if="statement.affected_rows !== null && statement.affected_rows !== undefined">
                                {{ t("approval.affectedRows", { rows: statement.affected_rows }) }}
                              </span>
                            </div>
                            <pre class="mt-2 overflow-x-auto text-[11px] leading-6">{{ statement.statement_text }}</pre>
                            <p v-if="statement.db_error_message" class="mt-2 text-xs text-destructive">
                              {{ statement.db_error_message }}
                            </p>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </ScrollArea>

                <div v-else class="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted-foreground">
                  <CircleAlert class="h-5 w-5" />
                  {{ t("approval.emptySelection") }}
                </div>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </SheetContent>
  </Sheet>

  <Dialog v-model:open="decisionOpen">
    <DialogContent class="sm:max-w-[480px]">
      <DialogHeader>
        <DialogTitle>
          {{ t(decisionMode === "approve" ? "approval.decisionTitleApprove" : "approval.decisionTitleReject") }}
        </DialogTitle>
      </DialogHeader>
      <div class="space-y-2">
        <Label for="approval-decision-comment">{{ t("approval.decisionComment") }}</Label>
        <textarea
          id="approval-decision-comment"
          v-model="decisionComment"
          class="min-h-[140px] w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          :placeholder="t('approval.decisionCommentPlaceholder')"
        />
      </div>
      <DialogFooter>
        <Button variant="outline" @click="decisionOpen = false">{{ t("dangerDialog.cancel") }}</Button>
        <Button :variant="decisionMode === 'approve' ? 'default' : 'destructive'" :disabled="actionLoading" @click="confirmDecision">
          <Loader2 v-if="actionLoading" class="mr-2 h-4 w-4 animate-spin" />
          {{ t(decisionMode === "approve" ? "approval.confirmApprove" : "approval.confirmReject") }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>
