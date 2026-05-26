<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RecycleScroller } from "vue-virtual-scroller";
import {
  ArrowDown,
  ArrowRightLeft,
  ArrowUp,
  Braces,
  Code2,
  Copy,
  CopyPlus,
  Download,
  Eraser,
  Eye,
  FileCode,
  FileUp,
  Loader2,
  Network,
  Pencil,
  PencilLine,
  PencilRuler,
  RefreshCw,
  Scissors,
  Search,
  ScrollText,
  Table2,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-vue-next";
import { useI18n } from "vue-i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import DangerConfirmDialog from "@/components/editor/DangerConfirmDialog.vue";
import * as api from "@/lib/api";
import type { ConnectionConfig, ObjectInfo, ObjectSourceKind } from "@/types/database";
import { isSchemaAware } from "@/lib/databaseCapabilities";
import {
  supportsSchemaDiagram,
  supportsTableImport,
  supportsTableStructureEditing,
  supportsTableTruncate,
} from "@/lib/databaseFeatureSupport";
import { buildTableSelectSql } from "@/lib/tableSelectSql";
import {
  buildDropObjectSql,
  buildDuplicateTableStructureSql,
  buildEmptyTableSql,
  buildTruncateTableSql,
  type TableAdminSqlOptions,
} from "@/lib/dbAdminSql";
import { useToast } from "@/composables/useToast";
import {
  buildExecutableObjectSourceStatements,
  buildRoutineRenameObjectSourceStatements,
  objectSourceSaveExecutionMode,
  supportsSourceBackedRoutineRename,
} from "@/lib/objectSourceEditor";
import { buildRenameObjectSql, supportsObjectRename } from "@/lib/objectRenameSql";
import { buildViewDdl } from "@/lib/viewDdl";
import { copyToClipboard } from "@/lib/clipboard";
import { formatSqlInsert } from "@/lib/exportFormats";
import { fetchTableDataForExport } from "@/lib/tableDataExport";
import { useConnectionStore } from "@/stores/connectionStore";
import { useQueryStore } from "@/stores/queryStore";
import QueryEditor from "@/components/editor/QueryEditor.vue";
import type { SqlFormatDialect } from "@/lib/sqlFormatter";
import { isCancelSearchShortcut } from "@/lib/keyboardShortcuts";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  buildObjectBrowserRows,
  filterObjectBrowserRows,
  formatObjectBrowserTimestamp,
  initialObjectBrowserSortDirection,
  sortObjectBrowserRows,
  type ObjectBrowserRow,
  type ObjectBrowserSortDirection,
  type ObjectBrowserSortKey,
} from "@/lib/objectBrowserRows";

type ObjectFilter = "all" | "tables" | "views" | "procedures" | "functions";

const props = defineProps<{
  connection: ConnectionConfig;
  database: string;
  schema?: string;
}>();

const emit = defineEmits<{
  openTable: [target: { tableName: string; schema?: string }];
  schemaChange: [schema: string | undefined];
}>();

const { t } = useI18n();
const { toast } = useToast();
const connectionStore = useConnectionStore();
const queryStore = useQueryStore();

const schemas = ref<string[]>([]);
const selectedSchema = ref<string | undefined>(props.schema);
const rows = ref<ObjectBrowserRow[]>([]);
const rootRef = ref<HTMLElement>();
const search = ref("");
const objectFilter = ref<ObjectFilter>("all");
const userHasSelectedFilter = ref(false);
const sortKey = ref<ObjectBrowserSortKey>("name");
const sortDirection = ref<ObjectBrowserSortDirection>("asc");
const loadingSchemas = ref(false);
const loadingObjects = ref(false);
const sourceLoading = ref(false);
const sourceContent = ref("");
const sourceError = ref("");
const sourceRow = ref<ObjectBrowserRow | null>(null);
const sourceEditing = ref(false);
const sourceDraft = ref("");
const sourceSaving = ref(false);
const sourceSaveError = ref("");
const error = ref("");
const showDropConfirm = ref(false);
const dropTarget = ref<ObjectBrowserRow | null>(null);
const showRenameDialog = ref(false);
const renameTarget = ref<ObjectBrowserRow | null>(null);
const renameInput = ref("");
const renameError = ref("");
const renamePreviewSqlText = ref("");
const showTruncateConfirm = ref(false);
const truncateTarget = ref<ObjectBrowserRow | null>(null);
const truncatePreviewSql = ref("");
const showEmptyConfirm = ref(false);
const emptyTarget = ref<ObjectBrowserRow | null>(null);
const emptyPreviewSql = ref("");
const showDuplicateDialog = ref(false);
const duplicateTarget = ref<ObjectBrowserRow | null>(null);
const duplicateTableName = ref("");
let loadId = 0;

const needsSchema = computed(() => isSchemaAware(props.connection.db_type));
const tableCount = computed(() => rows.value.filter((row) => row.type === "TABLE").length);
const viewCount = computed(() => rows.value.filter((row) => row.type === "VIEW").length);
const procedureCount = computed(() => rows.value.filter((row) => row.type === "PROCEDURE").length);
const functionCount = computed(() => rows.value.filter((row) => row.type === "FUNCTION").length);
const canOpenStructureEditor = computed(() => supportsTableStructureEditing(props.connection.db_type));
const canOpenDiagram = computed(() => !!props.database && supportsSchemaDiagram(props.connection.db_type));
const canOpenTableImport = computed(() => !!props.database && supportsTableImport(props.connection.db_type));
const supportsTruncateTable = computed(() => supportsTableTruncate(props.connection.db_type));
const sourceDialect = computed<"mysql" | "postgres" | "sqlserver">(() => {
  if (props.connection.db_type === "postgres" || props.connection.db_type === "gaussdb") return "postgres";
  if (props.connection.db_type === "sqlserver") return "sqlserver";
  return "mysql";
});
const sourceFormatDialect = computed<SqlFormatDialect>(() => {
  switch (props.connection.db_type) {
    case "mysql":
    case "postgres":
    case "sqlite":
    case "sqlserver":
      return props.connection.db_type;
    default:
      return "generic";
  }
});
const objectFilters = computed<ObjectFilter[]>(() =>
  (
    [
      ["all", rows.value.length],
      ["tables", tableCount.value],
      ["views", viewCount.value],
      ["procedures", procedureCount.value],
      ["functions", functionCount.value],
    ] as Array<[ObjectFilter, number]>
  )
    .filter(([filter, count]) => filter === "all" || count > 0)
    .map(([filter]) => filter),
);
const showObjectFilter = computed(() => objectFilters.value.length > 2);
const hasComments = computed(() => rows.value.some((row) => row.comment?.trim()));
const hasCreatedAt = computed(() => rows.value.some((row) => row.created_at?.trim()));
const hasUpdatedAt = computed(() => rows.value.some((row) => row.updated_at?.trim()));
const gridTemplateColumns = computed(() => {
  const columns = ["minmax(0,1fr)", "120px"];
  if (hasCreatedAt.value) columns.push("150px");
  if (hasUpdatedAt.value) columns.push("150px");
  if (hasComments.value) columns.push("minmax(160px,0.7fr)");
  return columns.join(" ");
});
const searchedRows = computed(() => {
  return filterObjectBrowserRows(rows.value, search.value);
});
const filteredRows = computed(() => {
  let rows = searchedRows.value;
  if (objectFilter.value === "tables") rows = rows.filter((row) => row.type === "TABLE");
  if (objectFilter.value === "views") rows = rows.filter((row) => row.type === "VIEW");
  if (objectFilter.value === "procedures") rows = rows.filter((row) => row.type === "PROCEDURE");
  if (objectFilter.value === "functions") rows = rows.filter((row) => row.type === "FUNCTION");
  return sortObjectBrowserRows(rows, sortKey.value, sortDirection.value);
});

function iconFor(row: ObjectBrowserRow) {
  if (row.type === "VIEW") return Eye;
  if (row.type === "PROCEDURE") return ScrollText;
  if (row.type === "FUNCTION") return Braces;
  return Table2;
}

function typeLabel(type: ObjectBrowserRow["type"]) {
  if (type === "VIEW") return t("objects.view");
  if (type === "PROCEDURE") return t("objects.procedure");
  if (type === "FUNCTION") return t("objects.function");
  return t("objects.table");
}

function sortIconFor(key: ObjectBrowserSortKey) {
  if (sortKey.value !== key) return null;
  return sortDirection.value === "asc" ? ArrowUp : ArrowDown;
}

function toggleSort(key: ObjectBrowserSortKey) {
  if (sortKey.value === key) {
    sortDirection.value = sortDirection.value === "asc" ? "desc" : "asc";
    return;
  }
  sortKey.value = key;
  sortDirection.value = initialObjectBrowserSortDirection(key);
}

function iconClass(type: ObjectBrowserRow["type"]) {
  if (type === "VIEW") return "text-purple-500";
  if (type === "PROCEDURE") return "text-blue-500";
  if (type === "FUNCTION") return "text-amber-500";
  return "text-green-500";
}

function canOpenSource(row: ObjectBrowserRow) {
  return row.type === "VIEW" || row.type === "PROCEDURE" || row.type === "FUNCTION";
}

function canRename(row: ObjectBrowserRow) {
  return (
    supportsObjectRename(props.connection.db_type, row.type) ||
    supportsSourceBackedRoutineRename(props.connection.db_type, row.type as ObjectSourceKind)
  );
}

function sourceTitle(row: ObjectBrowserRow | null) {
  if (!row) return t("objects.source");
  return `${row.name} ${t("objects.source")}`;
}

function openRow(row: ObjectBrowserRow) {
  if (row.type === "TABLE") {
    emit("openTable", { tableName: row.name, schema: row.schema });
    return;
  }
  if (canOpenSource(row)) {
    void openSource(row);
  }
}

async function openSource(row: ObjectBrowserRow) {
  sourceRow.value = row;
  sourceContent.value = "";
  sourceError.value = "";
  sourceEditing.value = false;
  sourceDraft.value = "";
  sourceSaveError.value = "";
  sourceLoading.value = true;
  try {
    const result = await api.getObjectSource(
      props.connection.id,
      props.database,
      row.schema || selectedSchema.value || props.database,
      row.name,
      row.type as ObjectSourceKind,
    );
    sourceContent.value = result.source;
    sourceDraft.value = result.source;
    sourceEditing.value = true;
  } catch (e: any) {
    sourceError.value = e?.message || String(e);
  } finally {
    sourceLoading.value = false;
  }
}

async function openViewDdl(row: ObjectBrowserRow) {
  if (row.type !== "VIEW") return;
  try {
    const result = await api.getObjectSource(
      props.connection.id,
      props.database,
      row.schema || selectedSchema.value || props.database,
      row.name,
      "VIEW",
    );
    const ddl = await buildViewDdl({
      databaseType: props.connection.db_type,
      schema: row.schema || selectedSchema.value || props.database,
      name: row.name,
      source: result.source,
    });
    const tabId = queryStore.createTab(props.connection.id, props.database, `DDL - ${row.name}`);
    queryStore.updateSql(tabId, ddl);
  } catch (e: any) {
    toast(e?.message || String(e), 5000);
  }
}

async function openNewQuery(row: ObjectBrowserRow) {
  const tabId = queryStore.createTab(props.connection.id, props.database, row.name);
  queryStore.updateSql(
    tabId,
    await buildTableSelectSql({
      databaseType: props.connection.db_type,
      schema: row.schema || selectedSchema.value,
      tableName: row.name,
      limit: 100,
    }),
  );
}

function requestDrop(row: ObjectBrowserRow) {
  dropTarget.value = row;
  showDropConfirm.value = true;
}

function requestRename(row: ObjectBrowserRow) {
  renameTarget.value = row;
  renameInput.value = row.name;
  renameError.value = "";
  renamePreviewSqlText.value = "";
  showRenameDialog.value = true;
}

let renamePreviewRequestId = 0;

async function refreshRenamePreviewSql() {
  const requestId = ++renamePreviewRequestId;
  const row = renameTarget.value;
  const newName = renameInput.value.trim();
  if (!showRenameDialog.value || !row || !newName || newName === row.name) {
    renamePreviewSqlText.value = "";
    return;
  }
  if (supportsSourceBackedRoutineRename(props.connection.db_type, row.type as ObjectSourceKind)) {
    renamePreviewSqlText.value = `-- Recreate ${row.type} from source, then drop the original object.`;
    return;
  }
  try {
    const sql = await buildRenameObjectSql({
      databaseType: props.connection.db_type,
      objectType: row.type,
      schema: row.schema || selectedSchema.value,
      oldName: row.name,
      newName,
    });
    if (requestId === renamePreviewRequestId) renamePreviewSqlText.value = sql;
  } catch {
    if (requestId === renamePreviewRequestId) renamePreviewSqlText.value = "";
  }
}

watch([showRenameDialog, renameTarget, renameInput, selectedSchema], () => {
  void refreshRenamePreviewSql();
});

async function confirmRename() {
  const row = renameTarget.value;
  const newName = renameInput.value.trim();
  if (!row || !newName || newName === row.name) return;
  renameError.value = "";
  try {
    const schema = row.schema || selectedSchema.value || props.database;
    if (supportsSourceBackedRoutineRename(props.connection.db_type, row.type as ObjectSourceKind)) {
      const source = await api.getObjectSource(
        props.connection.id,
        props.database,
        schema,
        row.name,
        row.type as ObjectSourceKind,
      );
      const statements = await buildRoutineRenameObjectSourceStatements({
        databaseType: props.connection.db_type,
        objectType: row.type as ObjectSourceKind,
        schema,
        name: row.name,
        newName,
        source: source.source,
      });
      for (const sql of statements) {
        await api.executeQuery(props.connection.id, props.database, sql, schema);
      }
    } else {
      const sql = await buildRenameObjectSql({
        databaseType: props.connection.db_type,
        objectType: row.type,
        schema,
        oldName: row.name,
        newName,
      });
      await api.executeQuery(props.connection.id, props.database, sql, schema);
    }
    toast(t("contextMenu.renameObjectSuccess", { oldName: row.name, newName }));
    showRenameDialog.value = false;
    if (sourceRow.value?.id === row.id) closeSource();
    await reload();
    await connectionStore.refreshObjectListTreeNode(
      props.connection.id,
      props.database,
      row.schema || selectedSchema.value,
    );
  } catch (e: any) {
    renameError.value = e?.message || String(e);
  }
}

async function confirmDrop() {
  if (!dropTarget.value) return;
  const row = dropTarget.value;
  try {
    const sql = await buildDropObjectSql({
      databaseType: props.connection.db_type,
      objectType: row.type,
      schema: row.schema || selectedSchema.value,
      name: row.name,
    });
    await api.executeQuery(props.connection.id, props.database, sql);
    const successKey =
      row.type === "VIEW"
        ? "contextMenu.dropViewSuccess"
        : row.type === "PROCEDURE"
          ? "contextMenu.dropProcedureSuccess"
          : row.type === "FUNCTION"
            ? "contextMenu.dropFunctionSuccess"
            : "contextMenu.dropTableSuccess";
    toast(t(successKey, { name: row.name }));
    await reload();
    await connectionStore.refreshObjectListTreeNode(
      props.connection.id,
      props.database,
      row.schema || selectedSchema.value,
    );
  } catch (e: any) {
    toast(t("contextMenu.tableOperationFailed", { message: e?.message || String(e) }), 5000);
  }
  dropTarget.value = null;
}

function dropConfirmTitle(): string {
  if (!dropTarget.value) return "";
  const type = dropTarget.value.type;
  if (type === "VIEW") return t("contextMenu.confirmDropViewTitle");
  if (type === "PROCEDURE") return t("contextMenu.confirmDropProcedureTitle");
  if (type === "FUNCTION") return t("contextMenu.confirmDropFunctionTitle");
  return t("contextMenu.confirmDropTableTitle");
}

function dropConfirmMessage(): string {
  if (!dropTarget.value) return "";
  const name = dropTarget.value.name;
  const type = dropTarget.value.type;
  if (type === "VIEW") return t("contextMenu.confirmDropViewMessage", { name });
  if (type === "PROCEDURE") return t("contextMenu.confirmDropProcedureMessage", { name });
  if (type === "FUNCTION") return t("contextMenu.confirmDropFunctionMessage", { name });
  return t("contextMenu.confirmDropTableMessage", { name });
}

function closeSource() {
  sourceRow.value = null;
  sourceContent.value = "";
  sourceError.value = "";
  sourceEditing.value = false;
  sourceDraft.value = "";
  sourceSaveError.value = "";
}

async function saveFileContent(content: string, defaultFileName: string) {
  const blob = new Blob([content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = defaultFileName;
  a.click();
  URL.revokeObjectURL(url);
}

function openViewData(row: ObjectBrowserRow) {
  emit("openTable", { tableName: row.name, schema: row.schema });
}

function openStructureEditor(row: ObjectBrowserRow) {
  if (row.type !== "TABLE") return;
  connectionStore.structureEditorSource = {
    connectionId: props.connection.id,
    database: props.database,
    schema: row.schema || selectedSchema.value,
    tableName: row.name,
  };
}

function openDiagram(row: ObjectBrowserRow) {
  connectionStore.diagramSource = {
    connectionId: props.connection.id,
    database: props.database,
    schema: row.schema || selectedSchema.value,
    tableName: row.type === "TABLE" ? row.name : undefined,
  };
}

function openTableImport(row: ObjectBrowserRow) {
  if (row.type !== "TABLE") return;
  connectionStore.tableImportSource = {
    connectionId: props.connection.id,
    database: props.database,
    schema: row.schema || selectedSchema.value,
    tableName: row.name,
  };
}

function openDataCompare(row: ObjectBrowserRow) {
  connectionStore.dataCompareSource = {
    connectionId: props.connection.id,
    database: props.database,
    schema: row.schema || selectedSchema.value,
    tableName: row.type === "TABLE" ? row.name : undefined,
  };
}

function openDatabaseExport(row: ObjectBrowserRow) {
  connectionStore.databaseExportSource = {
    connectionId: props.connection.id,
    database: props.database,
    schema: row.schema || selectedSchema.value,
    tableName: row.type === "TABLE" || row.type === "VIEW" ? row.name : undefined,
  };
}

async function exportStructure(row: ObjectBrowserRow) {
  try {
    const schema = row.schema || selectedSchema.value || props.database;
    const ddl = await api.getTableDdl(props.connection.id, props.database, schema, row.name);
    await saveFileContent(ddl + "\n", `${row.name}.sql`);
  } catch (e: any) {
    console.error("Export structure failed:", e);
  }
}

async function exportData(row: ObjectBrowserRow, format: "csv" | "json" | "sql") {
  try {
    const schema = row.schema || selectedSchema.value;
    const queryColumns =
      props.connection.db_type === "neo4j"
        ? (await api.getColumns(props.connection.id, props.database, schema || props.database, row.name)).map(
            (column) => column.name,
          )
        : undefined;
    const result = await fetchTableDataForExport({
      databaseType: props.connection.db_type,
      schema,
      tableName: row.name,
      columns: queryColumns,
      executePage: (sql) => api.executeQuery(props.connection.id, props.database, sql),
    });

    if (format === "csv") {
      const outputPath = `${row.name}.csv`;
      await api.exportQueryResultCsv(outputPath, result.columns, result.rows);
      toast(t("grid.exported"));
      return;
    }

    if (format === "json") {
      const outputPath = `${row.name}.json`;
      await api.exportQueryResultJson(outputPath, result.columns, result.rows);
      toast(t("grid.exported"));
      return;
    }

    const content = await formatSqlInsert({
      databaseType: props.connection.db_type,
      schema,
      tableName: row.name,
      columns: result.columns,
      rows: result.rows,
    });
    await saveFileContent(content, `${row.name}.sql`);
    toast(t("grid.exported"));
  } catch (e: any) {
    toast(t("grid.exportFailed", { message: e?.message || String(e) }), 5000);
  }
}

async function exportDataXlsx(row: ObjectBrowserRow) {
  try {
    const schema = row.schema || selectedSchema.value;
    const queryColumns =
      props.connection.db_type === "neo4j"
        ? (await api.getColumns(props.connection.id, props.database, schema || props.database, row.name)).map(
            (column) => column.name,
          )
        : undefined;
    const result = await fetchTableDataForExport({
      databaseType: props.connection.db_type,
      schema,
      tableName: row.name,
      columns: queryColumns,
      executePage: (sql) => api.executeQuery(props.connection.id, props.database, sql),
    });

    const outputPath = `${row.name}.xlsx`;
    await api.exportQueryResultXlsx(outputPath, row.name, result.columns, result.rows);
    toast(t("grid.exported"));
  } catch (e: any) {
    toast(t("grid.exportFailed", { message: e?.message || String(e) }), 5000);
  }
}

function requestDuplicateStructure(row: ObjectBrowserRow) {
  duplicateTarget.value = row;
  duplicateTableName.value = `${row.name}_copy`;
  showDuplicateDialog.value = true;
}

async function confirmDuplicateStructure() {
  const row = duplicateTarget.value;
  const newName = duplicateTableName.value.trim();
  if (!row || !newName) return;
  showDuplicateDialog.value = false;
  try {
    const schema = row.schema || selectedSchema.value;
    const sql = await buildDuplicateTableStructureSql({
      databaseType: props.connection.db_type,
      schema,
      sourceName: row.name,
      targetName: newName,
    });
    await api.executeQuery(props.connection.id, props.database, sql, schema);
    toast(t("contextMenu.duplicateStructureSuccess", { name: newName }));
    await reload();
    await connectionStore.refreshObjectListTreeNode(props.connection.id, props.database, schema);
  } catch (e: any) {
    toast(t("contextMenu.tableOperationFailed", { message: e?.message || String(e) }), 5000);
  }
}

function tableAdminSqlOptions(row: ObjectBrowserRow): TableAdminSqlOptions {
  return {
    databaseType: props.connection.db_type,
    schema: row.schema || selectedSchema.value,
    tableName: row.name,
  };
}

async function refreshTruncatePreviewSql(row: ObjectBrowserRow) {
  truncatePreviewSql.value = "";
  truncatePreviewSql.value = await buildTruncateTableSql(tableAdminSqlOptions(row)).catch(() => "");
}

function requestTruncateTable(row: ObjectBrowserRow) {
  truncateTarget.value = row;
  void refreshTruncatePreviewSql(row);
  showTruncateConfirm.value = true;
}

async function confirmTruncateTable() {
  const row = truncateTarget.value;
  if (!row) return;
  try {
    const sql = truncatePreviewSql.value || (await buildTruncateTableSql(tableAdminSqlOptions(row)));
    await api.executeQuery(props.connection.id, props.database, sql);
    toast(t("contextMenu.truncateTableSuccess", { name: row.name }));
  } catch (e: any) {
    toast(t("contextMenu.tableOperationFailed", { message: e?.message || String(e) }), 5000);
  }
  truncateTarget.value = null;
}

async function refreshEmptyPreviewSql(row: ObjectBrowserRow) {
  emptyPreviewSql.value = "";
  emptyPreviewSql.value = await buildEmptyTableSql(tableAdminSqlOptions(row)).catch(() => "");
}

function requestEmptyTable(row: ObjectBrowserRow) {
  emptyTarget.value = row;
  void refreshEmptyPreviewSql(row);
  showEmptyConfirm.value = true;
}

async function confirmEmptyTable() {
  const row = emptyTarget.value;
  if (!row) return;
  try {
    const sql = emptyPreviewSql.value || (await buildEmptyTableSql(tableAdminSqlOptions(row)));
    await api.executeQuery(props.connection.id, props.database, sql);
    toast(t("contextMenu.emptyTableSuccess", { name: row.name }));
  } catch (e: any) {
    toast(t("contextMenu.tableOperationFailed", { message: e?.message || String(e) }), 5000);
  }
  emptyTarget.value = null;
}

async function copyName(row: ObjectBrowserRow) {
  try {
    await copyToClipboard(row.name);
    toast(t("connection.copied"), 2000);
  } catch (e: any) {
    toast(t("grid.copyFailed", { message: e?.message || String(e) }), 5000);
  }
}

async function copySource() {
  if (!sourceContent.value) return;
  try {
    await copyToClipboard(sourceContent.value);
    toast(t("grid.copied"));
  } catch (e: any) {
    toast(t("grid.copyFailed", { message: e?.message || String(e) }), 5000);
  }
}

function editSource() {
  if (!sourceRow.value || !sourceContent.value) return;
  sourceDraft.value = sourceContent.value;
  sourceSaveError.value = "";
  sourceEditing.value = true;
}

function cancelEditSource() {
  sourceEditing.value = false;
  sourceDraft.value = "";
  sourceSaveError.value = "";
}

async function saveSource() {
  if (!sourceRow.value || !sourceDraft.value.trim()) return;
  const row = sourceRow.value;
  const schema = row.schema || selectedSchema.value || props.database;
  sourceSaving.value = true;
  sourceSaveError.value = "";
  try {
    const statements = await buildExecutableObjectSourceStatements({
      databaseType: props.connection.db_type,
      objectType: row.type as ObjectSourceKind,
      schema,
      name: row.name,
      source: sourceDraft.value,
    });
    for (const sql of statements) {
      if (objectSourceSaveExecutionMode(props.connection.db_type) === "single") {
        await api.executeQuery(props.connection.id, props.database, sql, schema);
      } else {
        await api.executeScript(props.connection.id, props.database, sql, schema);
      }
    }
    toast(t("objects.sourceSaved"));
    sourceEditing.value = false;
    sourceDraft.value = "";
    await openSource(row);
  } catch (e: any) {
    sourceSaveError.value = e?.message || String(e);
  } finally {
    sourceSaving.value = false;
  }
}

async function loadSchemas() {
  if (!needsSchema.value) {
    schemas.value = [];
    selectedSchema.value = undefined;
    return;
  }
  loadingSchemas.value = true;
  try {
    const names = await api.listSchemas(props.connection.id, props.database);
    schemas.value = names;
    if (!selectedSchema.value || !names.includes(selectedSchema.value)) {
      selectedSchema.value = names.includes("public") ? "public" : names[0];
    }
  } finally {
    loadingSchemas.value = false;
  }
}

async function loadObjects() {
  const id = ++loadId;
  loadingObjects.value = true;
  error.value = "";
  rows.value = [];
  try {
    const schema = needsSchema.value ? selectedSchema.value || "" : props.database;
    const objects: ObjectInfo[] = await api.listObjects(props.connection.id, props.database, schema);
    if (id !== loadId) return;
    rows.value = buildObjectBrowserRows({
      objects,
      database: props.database,
      fallbackSchema: schema,
      needsSchema: needsSchema.value,
    });
  } catch (e: any) {
    if (id !== loadId) return;
    error.value = e?.message || String(e);
  } finally {
    if (id === loadId) {
      loadingObjects.value = false;
      if (!userHasSelectedFilter.value && tableCount.value > 0) {
        objectFilter.value = "tables";
      }
    }
  }
}

async function reload() {
  await loadSchemas();
  await loadObjects();
}

function onSchemaChange(value: any) {
  selectedSchema.value = typeof value === "string" && value ? value : undefined;
  emit("schemaChange", selectedSchema.value);
  userHasSelectedFilter.value = false;
  objectFilter.value = "all";
  void loadObjects();
}

function filterCount(filter: ObjectFilter) {
  if (filter === "tables") return tableCount.value;
  if (filter === "views") return viewCount.value;
  if (filter === "procedures") return procedureCount.value;
  if (filter === "functions") return functionCount.value;
  return rows.value.length;
}

function filterLabel(filter: ObjectFilter) {
  const key =
    filter === "tables"
      ? "objects.tables"
      : filter === "views"
        ? "objects.views"
        : filter === "procedures"
          ? "objects.procedures"
          : filter === "functions"
            ? "objects.functions"
            : "objects.all";
  return `${t(key)} ${filterCount(filter)}`;
}

function getSearchInput(): HTMLInputElement | null {
  return rootRef.value?.querySelector<HTMLInputElement>("[data-object-search-input]") ?? null;
}

function focusSearch(): boolean {
  const input = getSearchInput();
  if (!input) return false;
  input.focus();
  input.select();
  return true;
}

function onSearchKeydown(event: KeyboardEvent) {
  if (!isCancelSearchShortcut(event)) return;
  event.preventDefault();
  search.value = "";
}

defineExpose({ focusSearch });

watch(
  () => [props.connection.id, props.database, props.schema] as const,
  () => {
    selectedSchema.value = props.schema;
    userHasSelectedFilter.value = false;
    objectFilter.value = "all";
    void reload();
  },
  { immediate: true },
);
</script>

<template>
  <div ref="rootRef" class="flex h-full min-h-0 flex-col bg-background">
    <div class="flex h-10 shrink-0 items-center gap-2 border-b px-3">
      <div class="flex min-w-0 flex-1 items-center gap-2">
        <Table2 class="h-4 w-4 text-muted-foreground" />
        <div class="min-w-0 truncate text-sm font-medium">
          {{ props.database }}<template v-if="selectedSchema"> / {{ selectedSchema }}</template>
        </div>
        <div class="shrink-0 rounded border bg-muted/40 px-1.5 py-0.5 text-xs text-muted-foreground">
          {{ filteredRows.length }} / {{ rows.length }}
        </div>
      </div>
      <div class="flex min-w-[240px] flex-1 items-center gap-2">
        <Search class="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <Input
          v-model="search"
          data-object-search-input
          class="h-7 text-xs"
          :placeholder="t('objects.search')"
          @keydown="onSearchKeydown"
        />
        <div v-if="showObjectFilter" class="flex h-7 shrink-0 items-center rounded border bg-muted/20 p-0.5">
          <button
            v-for="filter in objectFilters"
            :key="filter"
            type="button"
            class="h-6 rounded-sm px-2 text-xs text-muted-foreground transition-colors hover:text-foreground"
            :class="{ 'bg-background text-foreground shadow-sm': objectFilter === filter }"
            @click="
              userHasSelectedFilter = true;
              objectFilter = filter;
            "
          >
            {{ filterLabel(filter) }}
          </button>
        </div>
      </div>
      <Select
        v-if="needsSchema"
        :model-value="selectedSchema"
        :disabled="loadingSchemas"
        @update:model-value="onSchemaChange"
      >
        <SelectTrigger class="h-7 w-36 text-xs">
          <SelectValue :placeholder="loadingSchemas ? t('objects.loadingSchemas') : t('objects.schema')" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem v-for="schema in schemas" :key="schema" :value="schema">{{ schema }}</SelectItem>
        </SelectContent>
      </Select>
      <Button variant="ghost" size="icon" class="h-7 w-7" :disabled="loadingObjects" @click="reload">
        <RefreshCw class="h-3.5 w-3.5" :class="{ 'animate-spin': loadingObjects }" />
      </Button>
    </div>

    <div v-if="loadingObjects" class="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
      <Loader2 class="h-4 w-4 animate-spin" />
      {{ t("objects.loading") }}
    </div>
    <div v-else-if="error" class="flex flex-1 items-center justify-center px-6 text-center text-sm text-destructive">
      {{ error }}
    </div>
    <div
      v-else-if="filteredRows.length === 0"
      class="flex flex-1 items-center justify-center text-sm text-muted-foreground"
    >
      {{ t("objects.empty") }}
    </div>
    <div v-else class="flex min-h-0 flex-1 flex-col">
      <div
        class="grid h-8 shrink-0 items-center gap-3 border-b bg-muted/40 px-3 text-xs font-medium text-muted-foreground"
        :style="{ gridTemplateColumns }"
      >
        <button class="flex min-w-0 items-center gap-1 truncate text-left" type="button" @click="toggleSort('name')">
          <span class="truncate">{{ t("objects.name") }}</span>
          <component :is="sortIconFor('name')" v-if="sortIconFor('name')" class="h-3 w-3 shrink-0" />
        </button>
        <button class="flex min-w-0 items-center gap-1 truncate text-left" type="button" @click="toggleSort('type')">
          <span class="truncate">{{ t("objects.type") }}</span>
          <component :is="sortIconFor('type')" v-if="sortIconFor('type')" class="h-3 w-3 shrink-0" />
        </button>
        <button
          v-if="hasCreatedAt"
          class="flex min-w-0 items-center gap-1 truncate text-left"
          type="button"
          @click="toggleSort('created_at')"
        >
          <span class="truncate">{{ t("objects.createdAt") }}</span>
          <component :is="sortIconFor('created_at')" v-if="sortIconFor('created_at')" class="h-3 w-3 shrink-0" />
        </button>
        <button
          v-if="hasUpdatedAt"
          class="flex min-w-0 items-center gap-1 truncate text-left"
          type="button"
          @click="toggleSort('updated_at')"
        >
          <span class="truncate">{{ t("objects.updatedAt") }}</span>
          <component :is="sortIconFor('updated_at')" v-if="sortIconFor('updated_at')" class="h-3 w-3 shrink-0" />
        </button>
        <button
          v-if="hasComments"
          class="flex min-w-0 items-center gap-1 truncate text-left"
          type="button"
          @click="toggleSort('comment')"
        >
          <span class="truncate">{{ t("objects.comment") }}</span>
          <component :is="sortIconFor('comment')" v-if="sortIconFor('comment')" class="h-3 w-3 shrink-0" />
        </button>
      </div>
      <RecycleScroller
        class="object-browser-scroller min-h-0 flex-1"
        :items="filteredRows"
        :item-size="38"
        :buffer="600"
        :skip-hover="true"
        key-field="id"
      >
        <template #default="{ item }">
          <ContextMenu>
            <ContextMenuTrigger as-child>
              <div
                class="grid h-[38px] cursor-pointer items-center gap-3 border-b px-3 hover:bg-accent/50"
                :class="{ 'bg-accent/40': sourceRow?.id === item.id }"
                :style="{ gridTemplateColumns }"
                @click="openRow(item)"
              >
                <div class="flex min-w-0 items-center gap-2">
                  <component :is="iconFor(item)" class="h-3.5 w-3.5 shrink-0" :class="iconClass(item.type)" />
                  <span class="truncate text-[13px] font-medium text-foreground">{{ item.name }}</span>
                </div>
                <div class="truncate text-xs text-muted-foreground">{{ typeLabel(item.type) }}</div>
                <div
                  v-if="hasCreatedAt"
                  class="truncate text-xs tabular-nums text-muted-foreground"
                  :title="formatObjectBrowserTimestamp(item.created_at)"
                >
                  {{ formatObjectBrowserTimestamp(item.created_at) }}
                </div>
                <div
                  v-if="hasUpdatedAt"
                  class="truncate text-xs tabular-nums text-muted-foreground"
                  :title="formatObjectBrowserTimestamp(item.updated_at)"
                >
                  {{ formatObjectBrowserTimestamp(item.updated_at) }}
                </div>
                <div v-if="hasComments" class="truncate text-xs text-muted-foreground" :title="item.comment || ''">
                  {{ item.comment || "" }}
                </div>
              </div>
            </ContextMenuTrigger>
            <ContextMenuContent class="w-auto min-w-40">
              <!-- TABLE -->
              <template v-if="item.type === 'TABLE'">
                <ContextMenuItem @click="openRow(item)">
                  <Table2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.viewData") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canOpenStructureEditor" @click="openStructureEditor(item)">
                  <PencilRuler class="w-4 h-4 mr-2" /> {{ t("contextMenu.editStructure") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canRename(item)" @click="requestRename(item)">
                  <Pencil class="w-4 h-4 mr-2" /> {{ t("contextMenu.renameObject") }}
                </ContextMenuItem>
                <ContextMenuItem @click="openNewQuery(item)">
                  <TerminalSquare class="w-4 h-4 mr-2" /> {{ t("contextMenu.newQuery") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canOpenDiagram" @click="openDiagram(item)">
                  <Network class="w-4 h-4 mr-2" /> {{ t("diagram.open") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canOpenTableImport" @click="openTableImport(item)">
                  <FileUp class="w-4 h-4 mr-2" /> {{ t("contextMenu.importData") }}
                </ContextMenuItem>
                <ContextMenuItem @click="openDataCompare(item)">
                  <ArrowRightLeft class="w-4 h-4 mr-2" /> {{ t("dataCompare.title") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuSub>
                  <ContextMenuSubTrigger>
                    <Download class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportData") }}
                  </ContextMenuSubTrigger>
                  <ContextMenuSubContent>
                    <ContextMenuItem @click="exportData(item, 'csv')">CSV</ContextMenuItem>
                    <ContextMenuItem @click="exportData(item, 'json')">JSON</ContextMenuItem>
                    <ContextMenuItem @click="exportData(item, 'sql')">SQL INSERT</ContextMenuItem>
                    <ContextMenuItem @click="exportDataXlsx(item)">XLSX</ContextMenuItem>
                  </ContextMenuSubContent>
                </ContextMenuSub>
                <ContextMenuItem @click="openDatabaseExport(item)">
                  <Download class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportDatabase") }}
                </ContextMenuItem>
                <ContextMenuItem @click="exportStructure(item)">
                  <FileCode class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportStructure") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem @click="requestDuplicateStructure(item)">
                  <CopyPlus class="w-4 h-4 mr-2" /> {{ t("contextMenu.duplicateStructure") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem
                  v-if="supportsTruncateTable"
                  class="text-destructive"
                  @click="requestTruncateTable(item)"
                >
                  <Scissors class="w-4 h-4 mr-2" /> {{ t("contextMenu.truncateTable") }}
                </ContextMenuItem>
                <ContextMenuItem class="text-destructive" @click="requestEmptyTable(item)">
                  <Eraser class="w-4 h-4 mr-2" /> {{ t("contextMenu.emptyTable") }}
                </ContextMenuItem>
                <ContextMenuItem class="text-destructive" @click="requestDrop(item)">
                  <Trash2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.dropTable") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem @click="copyName(item)">
                  <Copy class="w-4 h-4 mr-2" /> {{ t("contextMenu.copyName") }}
                </ContextMenuItem>
              </template>
              <!-- VIEW -->
              <template v-else-if="item.type === 'VIEW'">
                <ContextMenuItem @click="openViewData(item)">
                  <Table2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.viewData") }}
                </ContextMenuItem>
                <ContextMenuItem @click="openSource(item)">
                  <Code2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.viewSource") }}
                </ContextMenuItem>
                <ContextMenuItem @click="openViewDdl(item)">
                  <ScrollText class="w-4 h-4 mr-2" /> {{ t("contextMenu.viewDdl") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canRename(item)" @click="requestRename(item)">
                  <Pencil class="w-4 h-4 mr-2" /> {{ t("contextMenu.renameObject") }}
                </ContextMenuItem>
                <ContextMenuItem @click="openNewQuery(item)">
                  <TerminalSquare class="w-4 h-4 mr-2" /> {{ t("contextMenu.newQuery") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canOpenDiagram" @click="openDiagram(item)">
                  <Network class="w-4 h-4 mr-2" /> {{ t("diagram.open") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuSub>
                  <ContextMenuSubTrigger>
                    <Download class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportData") }}
                  </ContextMenuSubTrigger>
                  <ContextMenuSubContent>
                    <ContextMenuItem @click="exportData(item, 'csv')">CSV</ContextMenuItem>
                    <ContextMenuItem @click="exportData(item, 'json')">JSON</ContextMenuItem>
                    <ContextMenuItem @click="exportData(item, 'sql')">SQL INSERT</ContextMenuItem>
                    <ContextMenuItem @click="exportDataXlsx(item)">XLSX</ContextMenuItem>
                  </ContextMenuSubContent>
                </ContextMenuSub>
                <ContextMenuItem @click="openDatabaseExport(item)">
                  <Download class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportDatabase") }}
                </ContextMenuItem>
                <ContextMenuItem @click="exportStructure(item)">
                  <FileCode class="w-4 h-4 mr-2" /> {{ t("contextMenu.exportStructure") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem class="text-destructive" @click="requestDrop(item)">
                  <Trash2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.dropView") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem @click="copyName(item)">
                  <Copy class="w-4 h-4 mr-2" /> {{ t("contextMenu.copyName") }}
                </ContextMenuItem>
              </template>
              <!-- PROCEDURE / FUNCTION -->
              <template v-else>
                <ContextMenuItem @click="openSource(item)">
                  <Code2 class="w-4 h-4 mr-2" /> {{ t("contextMenu.viewSource") }}
                </ContextMenuItem>
                <ContextMenuItem v-if="canRename(item)" @click="requestRename(item)">
                  <Pencil class="w-4 h-4 mr-2" /> {{ t("contextMenu.renameObject") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem class="text-destructive" @click="requestDrop(item)">
                  <Trash2 class="w-4 h-4 mr-2" />
                  {{ item.type === "PROCEDURE" ? t("contextMenu.dropProcedure") : t("contextMenu.dropFunction") }}
                </ContextMenuItem>
                <ContextMenuSeparator />
                <ContextMenuItem @click="copyName(item)">
                  <Copy class="w-4 h-4 mr-2" /> {{ t("contextMenu.copyName") }}
                </ContextMenuItem>
              </template>
            </ContextMenuContent>
          </ContextMenu>
        </template>
      </RecycleScroller>
      <div v-if="sourceRow" class="flex h-[42%] min-h-44 shrink-0 flex-col border-t bg-background">
        <div class="flex h-8 shrink-0 items-center gap-2 border-b bg-muted/20 px-3">
          <Code2 class="h-3.5 w-3.5 text-muted-foreground" />
          <span class="min-w-0 flex-1 truncate text-xs font-medium">{{ sourceTitle(sourceRow) }}</span>
          <Button
            v-if="sourceEditing"
            variant="ghost"
            size="sm"
            class="h-6 px-2 text-xs"
            :disabled="sourceSaving || !sourceDraft.trim()"
            @click="saveSource"
          >
            <Loader2 v-if="sourceSaving" class="mr-1 h-3 w-3 animate-spin" />
            {{ t("objects.saveSource") }}
          </Button>
          <Button
            v-if="sourceEditing"
            variant="ghost"
            size="sm"
            class="h-6 px-2 text-xs"
            :disabled="sourceSaving"
            @click="cancelEditSource"
          >
            {{ t("objects.cancelEdit") }}
          </Button>
          <Button
            v-if="!sourceEditing"
            variant="ghost"
            size="icon"
            class="h-5 w-5"
            :disabled="!sourceContent"
            @click="copySource"
          >
            <Copy class="h-3 w-3" />
          </Button>
          <Button
            v-if="!sourceEditing"
            variant="ghost"
            size="icon"
            class="h-5 w-5"
            :disabled="!sourceContent"
            @click="editSource"
          >
            <PencilLine class="h-3 w-3" />
          </Button>
          <Button variant="ghost" size="icon" class="h-5 w-5" @click="closeSource">
            <X class="h-3 w-3" />
          </Button>
        </div>
        <div v-if="sourceLoading" class="flex flex-1 items-center justify-center">
          <Loader2 class="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
        <div v-else-if="sourceError" class="flex flex-1 items-center justify-center px-4 text-sm text-destructive">
          {{ sourceError }}
        </div>
        <div v-else-if="sourceEditing" class="flex min-h-0 flex-1 flex-col" data-object-source-editor>
          <QueryEditor
            v-model="sourceDraft"
            class="min-h-0 flex-1"
            :connection-id="props.connection.id"
            :database="props.database"
            :dialect="sourceDialect"
            :format-dialect="sourceFormatDialect"
            force-word-wrap
            @save="saveSource"
          />
          <div v-if="sourceSaveError" class="shrink-0 border-t px-3 py-2 text-xs text-destructive">
            {{ sourceSaveError }}
          </div>
        </div>
        <QueryEditor
          v-else
          :key="`source-preview-${sourceRow.id}`"
          :model-value="sourceContent"
          class="min-h-0 flex-1"
          :connection-id="props.connection.id"
          :database="props.database"
          :dialect="sourceDialect"
          :format-dialect="sourceFormatDialect"
          force-word-wrap
          read-only
          data-object-source-preview
        />
      </div>
    </div>
  </div>

  <DangerConfirmDialog
    v-model:open="showDropConfirm"
    :title="dropConfirmTitle()"
    :details="dropConfirmMessage()"
    :confirm-label="t('dangerDialog.deleteConfirm')"
    @confirm="confirmDrop"
  />

  <Dialog v-model:open="showRenameDialog">
    <DialogContent class="sm:max-w-[420px]">
      <DialogHeader>
        <DialogTitle>{{ t("contextMenu.renameObjectTitle") }}</DialogTitle>
      </DialogHeader>
      <div class="grid gap-3">
        <Input
          v-model="renameInput"
          :placeholder="t('contextMenu.renameObjectNamePlaceholder')"
          @keydown.enter.prevent="confirmRename"
        />
        <pre
          v-if="renamePreviewSqlText"
          class="max-h-32 overflow-auto rounded bg-muted p-3 text-xs whitespace-pre-wrap"
          >{{ renamePreviewSqlText }}</pre
        >
        <p v-if="renameError" class="text-sm text-destructive">{{ renameError }}</p>
      </div>
      <DialogFooter>
        <Button variant="outline" @click="showRenameDialog = false">{{ t("dangerDialog.cancel") }}</Button>
        <Button :disabled="!renameInput.trim() || renameInput.trim() === renameTarget?.name" @click="confirmRename">
          {{ t("contextMenu.renameObject") }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>

  <DangerConfirmDialog
    v-model:open="showTruncateConfirm"
    :title="t('contextMenu.confirmTruncateTableTitle')"
    :message="t('contextMenu.confirmTruncateTableMessage', { name: truncateTarget?.name ?? '' })"
    :sql="truncatePreviewSql"
    :confirm-label="t('contextMenu.truncateTable')"
    @confirm="confirmTruncateTable"
  />

  <DangerConfirmDialog
    v-model:open="showEmptyConfirm"
    :title="t('contextMenu.confirmEmptyTableTitle')"
    :message="t('contextMenu.confirmEmptyTableMessage', { name: emptyTarget?.name ?? '' })"
    :sql="emptyPreviewSql"
    :confirm-label="t('contextMenu.emptyTable')"
    @confirm="confirmEmptyTable"
  />

  <Dialog v-model:open="showDuplicateDialog">
    <DialogContent class="sm:max-w-[400px]">
      <DialogHeader>
        <DialogTitle>{{ t("contextMenu.duplicateNameTitle") }}</DialogTitle>
      </DialogHeader>
      <Input
        v-model="duplicateTableName"
        :placeholder="t('contextMenu.duplicateNamePlaceholder')"
        @keydown.enter.prevent="confirmDuplicateStructure"
      />
      <DialogFooter>
        <Button variant="outline" @click="showDuplicateDialog = false">{{ t("dangerDialog.cancel") }}</Button>
        <Button :disabled="!duplicateTableName.trim()" @click="confirmDuplicateStructure">
          {{ t("dangerDialog.confirm") }}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>

<style scoped>
.object-browser-scroller {
  will-change: scroll-position;
  contain: content;
}

.object-browser-scroller :deep(.vue-recycle-scroller__item-view) {
  contain: layout style paint;
}
</style>
