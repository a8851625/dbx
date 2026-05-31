<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { Loader2, RefreshCw, Search, Shield } from "lucide-vue-next";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import * as api from "@/lib/api";
import type { AuditEventRecord, QueryAuditRecord } from "@/lib/api";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ "update:open": [value: boolean] }>();
const { t } = useI18n();

const openModel = computed({
  get: () => props.open,
  set: (value: boolean) => emit("update:open", value),
});

const activeTab = ref<"events" | "queries">("events");
const loading = ref(false);
const error = ref("");

const eventFilters = reactive({
  keyword: "",
  category: "",
  action: "",
  outcome: "",
  actor: "",
  resourceType: "",
  requestId: "",
  traceId: "",
});

const queryFilters = reactive({
  keyword: "",
  datasourceId: "",
  databaseName: "",
  status: "",
  operationType: "",
  actor: "",
  requestId: "",
  traceId: "",
});

const events = ref<AuditEventRecord[]>([]);
const queries = ref<QueryAuditRecord[]>([]);
const eventTotal = ref(0);
const queryTotal = ref(0);
const selectedEventId = ref<string | null>(null);
const selectedQueryId = ref<string | null>(null);

const selectedEvent = computed(() => events.value.find((item) => item.id === selectedEventId.value) ?? null);
const selectedQuery = computed(() => queries.value.find((item) => item.id === selectedQueryId.value) ?? null);

watch(
  () => props.open,
  (opened) => {
    if (opened) {
      void loadActiveTab();
    }
  },
);

watch(activeTab, () => {
  if (openModel.value) {
    void loadActiveTab();
  }
});

async function loadActiveTab() {
  loading.value = true;
  error.value = "";
  try {
    if (activeTab.value === "events") {
      const response = await api.listAuditEvents({
        limit: 80,
        keyword: eventFilters.keyword.trim() || undefined,
        category: eventFilters.category && eventFilters.category !== "all" ? eventFilters.category : undefined,
        action: eventFilters.action.trim() || undefined,
        outcome: eventFilters.outcome && eventFilters.outcome !== "all" ? eventFilters.outcome : undefined,
        actor: eventFilters.actor.trim() || undefined,
        resourceType: eventFilters.resourceType.trim() || undefined,
        requestId: eventFilters.requestId.trim() || undefined,
        traceId: eventFilters.traceId.trim() || undefined,
      });
      events.value = response.items;
      eventTotal.value = response.total;
      selectedEventId.value = response.items[0]?.id ?? null;
      return;
    }

    const response = await api.listQueryAudits({
      limit: 80,
      keyword: queryFilters.keyword.trim() || undefined,
      datasourceId: queryFilters.datasourceId.trim() || undefined,
      databaseName: queryFilters.databaseName.trim() || undefined,
      status: queryFilters.status && queryFilters.status !== "all" ? queryFilters.status : undefined,
      operationType:
        queryFilters.operationType && queryFilters.operationType !== "all" ? queryFilters.operationType : undefined,
      actor: queryFilters.actor.trim() || undefined,
      requestId: queryFilters.requestId.trim() || undefined,
      traceId: queryFilters.traceId.trim() || undefined,
    });
    queries.value = response.items;
    queryTotal.value = response.total;
    selectedQueryId.value = response.items[0]?.id ?? null;
  } catch (e: any) {
    error.value = e?.message || String(e);
  } finally {
    loading.value = false;
  }
}

function formatDateTime(value?: string | null) {
  if (!value) return t("audit.notAvailable");
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function badgeVariant(value: string) {
  if (["failed", "error", "failure", "reject", "rejected"].includes(value)) return "destructive";
  if (["success", "succeeded", "approved"].includes(value)) return "secondary";
  return "outline";
}

function prettyJson(value: unknown) {
  return JSON.stringify(value ?? {}, null, 2);
}
</script>

<template>
  <Sheet v-model:open="openModel">
    <SheetContent side="right" class="w-full max-w-[1120px] p-0 sm:max-w-[1120px]">
      <div class="flex h-full flex-col">
        <SheetHeader class="border-b px-6 py-4 text-left">
          <SheetTitle class="flex items-center gap-2">
            <Shield class="h-4 w-4" />
            {{ t("audit.title") }}
          </SheetTitle>
          <SheetDescription>{{ t("audit.description") }}</SheetDescription>
        </SheetHeader>

        <Tabs v-model="activeTab" class="flex min-h-0 flex-1 flex-col">
          <div class="border-b px-6 py-3">
            <div class="flex items-center justify-between gap-4">
              <TabsList>
                <TabsTrigger value="events">{{ t("audit.eventsTab") }}</TabsTrigger>
                <TabsTrigger value="queries">{{ t("audit.queriesTab") }}</TabsTrigger>
              </TabsList>
              <Button variant="outline" size="sm" class="gap-1.5" :disabled="loading" @click="loadActiveTab">
                <Loader2 v-if="loading" class="h-3.5 w-3.5 animate-spin" />
                <RefreshCw v-else class="h-3.5 w-3.5" />
                {{ t("audit.refresh") }}
              </Button>
            </div>
          </div>

          <TabsContent value="events" class="m-0 flex min-h-0 flex-1 flex-col">
            <div class="border-b px-6 py-4">
              <div class="grid gap-3 md:grid-cols-4">
                <div class="space-y-1.5 md:col-span-2">
                  <Label for="audit-event-keyword">{{ t("audit.keyword") }}</Label>
                  <Input
                    id="audit-event-keyword"
                    v-model="eventFilters.keyword"
                    :placeholder="t('audit.keywordPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label>{{ t("audit.category") }}</Label>
                  <Select v-model="eventFilters.category">
                    <SelectTrigger><SelectValue :placeholder="t('audit.allCategories')" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{{ t("audit.allCategories") }}</SelectItem>
                      <SelectItem value="auth">auth</SelectItem>
                      <SelectItem value="access">access</SelectItem>
                      <SelectItem value="connection">connection</SelectItem>
                      <SelectItem value="config">config</SelectItem>
                      <SelectItem value="approval">approval</SelectItem>
                      <SelectItem value="execution">execution</SelectItem>
                      <SelectItem value="query">query</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div class="space-y-1.5">
                  <Label>{{ t("audit.outcome") }}</Label>
                  <Select v-model="eventFilters.outcome">
                    <SelectTrigger><SelectValue :placeholder="t('audit.allOutcomes')" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{{ t("audit.allOutcomes") }}</SelectItem>
                      <SelectItem value="success">success</SelectItem>
                      <SelectItem value="failure">failure</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div class="mt-3 grid gap-3 md:grid-cols-4">
                <div class="space-y-1.5">
                  <Label for="audit-event-actor">{{ t("audit.actor") }}</Label>
                  <Input
                    id="audit-event-actor"
                    v-model="eventFilters.actor"
                    :placeholder="t('audit.actorPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-event-action">{{ t("audit.action") }}</Label>
                  <Input
                    id="audit-event-action"
                    v-model="eventFilters.action"
                    placeholder="create / update / delete"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-event-resource-type">{{ t("audit.resourceType") }}</Label>
                  <Input
                    id="audit-event-resource-type"
                    v-model="eventFilters.resourceType"
                    placeholder="api / approval / permission"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-event-request-id">{{ t("audit.requestId") }}</Label>
                  <Input
                    id="audit-event-request-id"
                    v-model="eventFilters.requestId"
                    :placeholder="t('audit.requestIdPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5 md:col-span-2">
                  <Label for="audit-event-trace-id">{{ t("audit.traceId") }}</Label>
                  <Input
                    id="audit-event-trace-id"
                    v-model="eventFilters.traceId"
                    :placeholder="t('audit.traceIdPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="flex items-end">
                  <Button size="sm" class="gap-1.5" @click="loadActiveTab">
                    <Search class="h-3.5 w-3.5" />
                    {{ t("audit.search") }}
                  </Button>
                </div>
              </div>
            </div>

            <div class="grid min-h-0 flex-1 gap-0 md:grid-cols-[360px_1fr]">
              <div class="border-r">
                <div class="flex items-center justify-between px-4 py-3 text-sm text-muted-foreground">
                  <span>{{ t("audit.eventsCount", { count: eventTotal }) }}</span>
                </div>
                <ScrollArea class="h-[calc(100vh-240px)]">
                  <div v-if="events.length === 0" class="px-4 py-8 text-sm text-muted-foreground">
                    {{ error || t("audit.emptyEvents") }}
                  </div>
                  <button
                    v-for="item in events"
                    :key="item.id"
                    class="flex w-full flex-col gap-2 border-t px-4 py-3 text-left transition hover:bg-muted/40"
                    :class="{ 'bg-muted/50': selectedEventId === item.id }"
                    @click="selectedEventId = item.id"
                  >
                    <div class="flex items-center justify-between gap-2">
                      <Badge :variant="badgeVariant(item.outcome)">{{ item.outcome }}</Badge>
                      <span class="text-xs text-muted-foreground">{{ formatDateTime(item.created_at) }}</span>
                    </div>
                    <div class="text-sm font-medium">{{ item.event_type }}</div>
                    <div class="text-xs text-muted-foreground">
                      {{ item.actor_email || item.actor_display_name || t("audit.systemActor") }}
                    </div>
                    <div class="text-xs text-muted-foreground">
                      {{ item.resource_name || item.resource_type || "-" }}
                    </div>
                  </button>
                </ScrollArea>
              </div>

              <div class="min-h-0">
                <ScrollArea class="h-[calc(100vh-240px)]">
                  <div v-if="selectedEvent" class="space-y-5 px-6 py-5">
                    <div class="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{{ selectedEvent.category }}</Badge>
                      <Badge :variant="badgeVariant(selectedEvent.outcome)">{{ selectedEvent.outcome }}</Badge>
                      <Badge variant="outline">{{ selectedEvent.action }}</Badge>
                    </div>
                    <div>
                      <h3 class="text-lg font-semibold">{{ selectedEvent.event_type }}</h3>
                      <p class="mt-1 text-sm text-muted-foreground">{{ formatDateTime(selectedEvent.created_at) }}</p>
                    </div>
                    <Separator />
                    <div class="grid gap-4 md:grid-cols-2">
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.actor") }}</div>
                        <div class="mt-1 text-sm">
                          {{ selectedEvent.actor_email || selectedEvent.actor_display_name || t("audit.systemActor") }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.sourceIp") }}</div>
                        <div class="mt-1 text-sm">{{ selectedEvent.source_ip || t("audit.notAvailable") }}</div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.resource") }}</div>
                        <div class="mt-1 text-sm">
                          {{ selectedEvent.resource_type || "-" }} /
                          {{ selectedEvent.resource_name || selectedEvent.resource_id || "-" }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.request") }}</div>
                        <div class="mt-1 text-sm">
                          {{ selectedEvent.request_method || "-" }} {{ selectedEvent.request_path || "" }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.requestId") }}</div>
                        <div class="mt-1 break-all text-sm">
                          {{ selectedEvent.request_id || t("audit.notAvailable") }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.traceId") }}</div>
                        <div class="mt-1 break-all text-sm">
                          {{ selectedEvent.trace_id || t("audit.notAvailable") }}
                        </div>
                      </div>
                    </div>
                    <div>
                      <div class="text-xs text-muted-foreground">{{ t("audit.payload") }}</div>
                      <pre class="mt-2 overflow-x-auto rounded-md border bg-muted/20 p-3 text-xs leading-5">{{
                        prettyJson(selectedEvent.payload)
                      }}</pre>
                    </div>
                  </div>
                  <div v-else class="px-6 py-8 text-sm text-muted-foreground">{{ t("audit.emptySelection") }}</div>
                </ScrollArea>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="queries" class="m-0 flex min-h-0 flex-1 flex-col">
            <div class="border-b px-6 py-4">
              <div class="grid gap-3 md:grid-cols-4">
                <div class="space-y-1.5 md:col-span-2">
                  <Label for="audit-query-keyword">{{ t("audit.keyword") }}</Label>
                  <Input
                    id="audit-query-keyword"
                    v-model="queryFilters.keyword"
                    :placeholder="t('audit.queryKeywordPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-query-datasource">{{ t("audit.datasource") }}</Label>
                  <Input
                    id="audit-query-datasource"
                    v-model="queryFilters.datasourceId"
                    :placeholder="t('audit.datasourcePlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-query-database">{{ t("audit.database") }}</Label>
                  <Input
                    id="audit-query-database"
                    v-model="queryFilters.databaseName"
                    :placeholder="t('audit.databasePlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label>{{ t("audit.status") }}</Label>
                  <Select v-model="queryFilters.status">
                    <SelectTrigger><SelectValue :placeholder="t('audit.allStatuses')" /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{{ t("audit.allStatuses") }}</SelectItem>
                      <SelectItem value="succeeded">succeeded</SelectItem>
                      <SelectItem value="failed">failed</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div class="mt-3 grid gap-3 md:grid-cols-4">
                <div class="space-y-1.5">
                  <Label>{{ t("audit.operationType") }}</Label>
                  <Select v-model="queryFilters.operationType">
                    <SelectTrigger class="w-[220px]"
                      ><SelectValue :placeholder="t('audit.allOperationTypes')"
                    /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{{ t("audit.allOperationTypes") }}</SelectItem>
                      <SelectItem value="interactive">interactive</SelectItem>
                      <SelectItem value="multi">multi</SelectItem>
                      <SelectItem value="batch">batch</SelectItem>
                      <SelectItem value="script">script</SelectItem>
                      <SelectItem value="transaction">transaction</SelectItem>
                      <SelectItem value="internal_script">internal_script</SelectItem>
                      <SelectItem value="internal_transaction">internal_transaction</SelectItem>
                      <SelectItem value="approval_execution">approval_execution</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-query-actor">{{ t("audit.actor") }}</Label>
                  <Input
                    id="audit-query-actor"
                    v-model="queryFilters.actor"
                    :placeholder="t('audit.actorPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-query-request-id">{{ t("audit.requestId") }}</Label>
                  <Input
                    id="audit-query-request-id"
                    v-model="queryFilters.requestId"
                    :placeholder="t('audit.requestIdPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="space-y-1.5">
                  <Label for="audit-query-trace-id">{{ t("audit.traceId") }}</Label>
                  <Input
                    id="audit-query-trace-id"
                    v-model="queryFilters.traceId"
                    :placeholder="t('audit.traceIdPlaceholder')"
                    @keydown.enter.prevent="loadActiveTab"
                  />
                </div>
                <div class="flex items-end">
                  <Button size="sm" class="gap-1.5" @click="loadActiveTab">
                    <Search class="h-3.5 w-3.5" />
                    {{ t("audit.search") }}
                  </Button>
                </div>
              </div>
            </div>

            <div class="grid min-h-0 flex-1 gap-0 md:grid-cols-[360px_1fr]">
              <div class="border-r">
                <div class="flex items-center justify-between px-4 py-3 text-sm text-muted-foreground">
                  <span>{{ t("audit.queriesCount", { count: queryTotal }) }}</span>
                </div>
                <ScrollArea class="h-[calc(100vh-284px)]">
                  <div v-if="queries.length === 0" class="px-4 py-8 text-sm text-muted-foreground">
                    {{ error || t("audit.emptyQueries") }}
                  </div>
                  <button
                    v-for="item in queries"
                    :key="item.id"
                    class="flex w-full flex-col gap-2 border-t px-4 py-3 text-left transition hover:bg-muted/40"
                    :class="{ 'bg-muted/50': selectedQueryId === item.id }"
                    @click="selectedQueryId = item.id"
                  >
                    <div class="flex items-center justify-between gap-2">
                      <Badge :variant="badgeVariant(item.status)">{{ item.status }}</Badge>
                      <span class="text-xs text-muted-foreground">{{ formatDateTime(item.created_at) }}</span>
                    </div>
                    <div class="text-sm font-medium">{{ item.sql_summary || item.sql_text }}</div>
                    <div class="text-xs text-muted-foreground">{{ item.datasource_id }} / {{ item.database_name }}</div>
                    <div class="text-xs text-muted-foreground">
                      {{ item.actor_email || item.actor_display_name || t("audit.systemActor") }}
                    </div>
                  </button>
                </ScrollArea>
              </div>

              <div class="min-h-0">
                <ScrollArea class="h-[calc(100vh-284px)]">
                  <div v-if="selectedQuery" class="space-y-5 px-6 py-5">
                    <div class="flex flex-wrap items-center gap-2">
                      <Badge :variant="badgeVariant(selectedQuery.status)">{{ selectedQuery.status }}</Badge>
                      <Badge variant="outline">{{ selectedQuery.operation_type }}</Badge>
                      <Badge variant="outline">{{ selectedQuery.execution_mode }}</Badge>
                    </div>
                    <div>
                      <h3 class="text-lg font-semibold">
                        {{ selectedQuery.sql_summary || t("audit.queryDetailTitle") }}
                      </h3>
                      <p class="mt-1 text-sm text-muted-foreground">{{ formatDateTime(selectedQuery.created_at) }}</p>
                    </div>
                    <Separator />
                    <div class="grid gap-4 md:grid-cols-2">
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.actor") }}</div>
                        <div class="mt-1 text-sm">
                          {{ selectedQuery.actor_email || selectedQuery.actor_display_name || t("audit.systemActor") }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.sourceIp") }}</div>
                        <div class="mt-1 text-sm">{{ selectedQuery.source_ip || t("audit.notAvailable") }}</div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.datasource") }}</div>
                        <div class="mt-1 text-sm">{{ selectedQuery.datasource_id }}</div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.database") }}</div>
                        <div class="mt-1 text-sm">
                          {{ selectedQuery.database_name
                          }}<span v-if="selectedQuery.schema_name"> / {{ selectedQuery.schema_name }}</span>
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.duration") }}</div>
                        <div class="mt-1 text-sm">{{ selectedQuery.duration_ms ?? t("audit.notAvailable") }}</div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.affectedRows") }}</div>
                        <div class="mt-1 text-sm">{{ selectedQuery.affected_rows ?? t("audit.notAvailable") }}</div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.requestId") }}</div>
                        <div class="mt-1 break-all text-sm">
                          {{ selectedQuery.request_id || t("audit.notAvailable") }}
                        </div>
                      </div>
                      <div>
                        <div class="text-xs text-muted-foreground">{{ t("audit.traceId") }}</div>
                        <div class="mt-1 break-all text-sm">
                          {{ selectedQuery.trace_id || t("audit.notAvailable") }}
                        </div>
                      </div>
                      <div v-if="selectedQuery.error_code">
                        <div class="text-xs text-muted-foreground">{{ t("audit.errorCode") }}</div>
                        <div class="mt-1 text-sm text-destructive">{{ selectedQuery.error_code }}</div>
                      </div>
                    </div>
                    <div>
                      <div class="text-xs text-muted-foreground">{{ t("audit.sqlText") }}</div>
                      <pre class="mt-2 overflow-x-auto rounded-md border bg-muted/20 p-3 text-xs leading-5">{{
                        selectedQuery.sql_text
                      }}</pre>
                    </div>
                    <div>
                      <div class="text-xs text-muted-foreground">{{ t("audit.metadata") }}</div>
                      <pre class="mt-2 overflow-x-auto rounded-md border bg-muted/20 p-3 text-xs leading-5">{{
                        prettyJson(selectedQuery.metadata)
                      }}</pre>
                    </div>
                    <div v-if="selectedQuery.error_message">
                      <div class="text-xs text-muted-foreground">{{ t("audit.errorMessage") }}</div>
                      <pre
                        class="mt-2 overflow-x-auto rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs leading-5 text-destructive"
                        >{{ selectedQuery.error_message }}</pre
                      >
                    </div>
                  </div>
                  <div v-else class="px-6 py-8 text-sm text-muted-foreground">{{ t("audit.emptySelection") }}</div>
                </ScrollArea>
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </SheetContent>
  </Sheet>
</template>
