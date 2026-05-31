import { ref, type Ref, type ComputedRef } from "vue";
import { useI18n } from "vue-i18n";
import { useQueryStore } from "@/stores/queryStore";
import { useHistoryStore } from "@/stores/historyStore";
import { useConnectionStore } from "@/stores/connectionStore";
import { useToast } from "@/composables/useToast";
import * as api from "@/lib/api";
import { classifySqlActivityKind } from "@/lib/historyActivityKind";
import { sqlMetadataRefreshTarget } from "@/lib/sqlMetadataRefresh";
import type { ConnectionConfig, QueryTab } from "@/types/database";

const DANGER_RE = /^\s*(DROP|DELETE|TRUNCATE|ALTER|UPDATE|MERGE|REPLACE)\b/i;

export function stripSqlComments(sql: string): string {
  return sql
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/--.*$/gm, " ")
    .replace(/#.*$/gm, " ");
}

export function isDangerousSql(sql: string): boolean {
  const cleaned = stripSqlComments(sql);
  return cleaned.split(";").some((stmt) => DANGER_RE.test(stmt));
}

export function requiresApprovalFromClassification(classification: api.QueryClassificationResponse): boolean {
  return (
    classification.requires_approval ||
    classification.statements.some((statement) => {
      const type = String(statement.statement_type || "").toLowerCase();
      return type === "ddl" || type === "dml";
    })
  );
}

export function approvalDraftSqlFromClassification(
  classification: api.QueryClassificationResponse,
  fallbackSql: string,
): string {
  const changeStatements = classification.statements
    .filter((statement) => {
      const type = String(statement.statement_type || "").toLowerCase();
      return type === "ddl" || type === "dml";
    })
    .map((statement) => statement.statement_text.trim())
    .filter(Boolean);
  return changeStatements.length ? changeStatements.join(";\n") : fallbackSql;
}

function primarySqlOperation(sql: string): string {
  const cleaned = stripSqlComments(sql);
  const statement = cleaned
    .split(";")
    .map((part) => part.trim())
    .find(Boolean);
  return statement?.match(/^([a-z]+)/i)?.[1]?.toUpperCase() || "SQL";
}

export function useSqlExecution(deps: {
  activeTab: ComputedRef<QueryTab | undefined>;
  activeConnection: ComputedRef<ConnectionConfig | undefined>;
  executableSql: ComputedRef<string>;
  resolveExecutableSql?: () => Promise<string>;
  openApprovalDraft?: (draft: {
    sql: string;
    connectionId: string;
    database: string;
    schema?: string;
    title?: string;
  }) => void;
  activeOutputView: Ref<"result" | "explain" | "chart">;
}) {
  const { t } = useI18n();
  const queryStore = useQueryStore();
  const historyStore = useHistoryStore();
  const connectionStore = useConnectionStore();
  const { toast } = useToast();

  const dangerSql = ref("");
  const pendingDangerSql = ref("");
  const showDangerDialog = ref(false);

  async function resolvedExecutableSql(): Promise<string> {
    return deps.resolveExecutableSql ? await deps.resolveExecutableSql() : deps.executableSql.value;
  }

  async function redirectToApprovalIfRequired(tab: QueryTab, sql: string): Promise<boolean> {
    let classification: api.QueryClassificationResponse | undefined;
    try {
      classification = await api.classifyQuery(sql);
    } catch {
      // If the classifier is unavailable, the backend execution gate remains authoritative.
    }
    if (classification && requiresApprovalFromClassification(classification)) {
      deps.openApprovalDraft?.({
        sql: approvalDraftSqlFromClassification(classification, sql),
        connectionId: tab.connectionId,
        database: tab.database,
        schema: tab.schema,
        title: tab.title,
      });
      toast(t("approval.directExecutionBlocked"), 5000);
      return true;
    }
    return false;
  }

  async function tryExecute(sqlOverride?: string) {
    const tab = deps.activeTab.value;
    const sql = sqlOverride ?? (await resolvedExecutableSql());
    if (!tab || !sql.trim()) return;
    if (await redirectToApprovalIfRequired(tab, sql)) return;
    if (isDangerousSql(sql)) {
      dangerSql.value = sql;
      pendingDangerSql.value = sql;
      showDangerDialog.value = true;
    } else {
      doExecute(sql);
    }
  }

  async function doExecute(sql?: string) {
    sql ??= await resolvedExecutableSql();
    const tab = deps.activeTab.value;
    if (!tab || !sql.trim()) return;
    if (await redirectToApprovalIfRequired(tab, sql)) return;
    deps.activeOutputView.value = "result";
    const connName = connectionStore.getConfig(tab.connectionId)?.name || "";
    const start = Date.now();
    await queryStore.executeCurrentSql(sql);
    const elapsed = Date.now() - start;
    const success = !tab.result?.columns.includes("Error");
    historyStore.add({
      connection_id: tab.connectionId,
      connection_name: connName,
      database: tab.database,
      sql,
      execution_time_ms: elapsed,
      success,
      error: success ? undefined : String(tab.result?.rows?.[0]?.[0] ?? ""),
      activity_kind: classifySqlActivityKind(sql),
      operation: primarySqlOperation(sql),
      affected_rows: success ? tab.result?.affected_rows : undefined,
    });
    if (success) {
      const refreshTarget = sqlMetadataRefreshTarget(sql, tab.schema);
      if (refreshTarget.scope === "connection") {
        await connectionStore.loadDatabases(tab.connectionId, { force: true });
      } else if (refreshTarget.scope === "database") {
        await connectionStore.refreshObjectListTreeNode(tab.connectionId, tab.database, refreshTarget.schema);
      }
    }
  }

  function cancelActiveExecution() {
    const tab = deps.activeTab.value;
    if (!tab) return;
    if (tab.isExecuting) void queryStore.cancelTabExecution(tab.id);
    else if (tab.isExplaining) void queryStore.cancelTabExplain(tab.id);
  }

  function explainReasonMessage(reason: string): string {
    if (reason === "unsupported") return t("explain.unsupported");
    if (reason === "unsafe") return t("explain.unsafe");
    return t("explain.emptySql");
  }

  async function tryExplain(sqlOverride?: string) {
    const tab = deps.activeTab.value;
    const sql = sqlOverride ?? (await resolvedExecutableSql());
    if (!tab || !sql.trim()) {
      toast(t("explain.emptySql"));
      return;
    }

    deps.activeOutputView.value = "explain";
    const result = await queryStore.explainTabSql(tab.id, sql, deps.activeConnection.value?.db_type);
    if (!result.ok) {
      toast(explainReasonMessage(result.reason), 5000);
      return;
    }

    const current = deps.activeTab.value;
    if (current?.explainError) toast(current.explainError, 5000);
  }

  async function onDangerConfirm() {
    const sql = pendingDangerSql.value || (await resolvedExecutableSql());
    pendingDangerSql.value = "";
    showDangerDialog.value = false;
    await doExecute(sql);
  }

  return {
    dangerSql,
    pendingDangerSql,
    showDangerDialog,
    tryExecute,
    doExecute,
    cancelActiveExecution,
    tryExplain,
    onDangerConfirm,
  };
}
