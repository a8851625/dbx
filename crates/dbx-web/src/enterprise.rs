use std::sync::Arc;

use axum::body::{to_bytes, Body};
use axum::extract::State;
use axum::http::{header, HeaderMap, Method, Request, StatusCode};
use axum::response::{IntoResponse, Response};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::state::{EnterpriseBridge, WebState};

const MAX_PROXY_BODY_BYTES: usize = 300 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize)]
pub struct AccessPolicy {
    pub resource_type: String,
    pub resource_key: String,
    pub permission_code: Option<String>,
    pub effect: String,
    pub conditions: Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AccessContext {
    pub roles: Vec<String>,
    pub permissions: Vec<String>,
    pub policies: Vec<AccessPolicy>,
}

#[derive(Debug, Serialize)]
pub struct AccessCheckRequest {
    pub permission: String,
    pub datasource_id: Option<String>,
    pub database: Option<String>,
    pub schema: Option<String>,
    pub table: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AccessCheckResponse {
    pub allowed: bool,
    pub reason: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InternalQueryAuditRequest {
    pub session_token: Option<String>,
    pub execution_id: Option<String>,
    pub datasource_id: String,
    pub database: String,
    pub schema: Option<String>,
    pub table: Option<String>,
    pub operation_type: String,
    pub execution_mode: String,
    pub statement_count: usize,
    pub sql_text: String,
    pub status: String,
    pub duration_ms: Option<u64>,
    pub affected_rows: Option<u64>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub source_ip: Option<String>,
    pub user_agent: Option<String>,
    pub request_path: Option<String>,
    pub request_method: Option<String>,
    pub metadata: Value,
}

pub async fn proxy_request(State(state): State<Arc<WebState>>, req: Request<Body>) -> Response {
    let Some(enterprise) = state.enterprise.as_ref() else {
        return StatusCode::NOT_FOUND.into_response();
    };

    match proxy_with_client(&enterprise.client, &enterprise.base_url, req).await {
        Ok(response) => response,
        Err(error) => (StatusCode::BAD_GATEWAY, error).into_response(),
    }
}

pub async fn fetch_access_context(enterprise: &EnterpriseBridge, headers: &HeaderMap) -> Result<AccessContext, String> {
    let mut request = enterprise.client.get(format!("{}/api/v1/access/me", enterprise.base_url));
    if let Some(cookie) = headers.get(header::COOKIE).and_then(|value| value.to_str().ok()) {
        request = request.header(header::COOKIE, cookie);
    }

    let response = request.send().await.map_err(|error| format!("Failed to fetch access context: {error}"))?;
    if response.status() == StatusCode::UNAUTHORIZED {
        return Err("Authentication required".to_string());
    }
    if !response.status().is_success() {
        return Err(format!("Enterprise access context failed: {}", response.status()));
    }
    response.json().await.map_err(|error| format!("Invalid access context payload: {error}"))
}

pub async fn check_access(
    enterprise: &EnterpriseBridge,
    headers: &HeaderMap,
    payload: &AccessCheckRequest,
) -> Result<AccessCheckResponse, String> {
    let mut request = enterprise.client.post(format!("{}/api/v1/access/check", enterprise.base_url)).json(payload);
    if let Some(cookie) = headers.get(header::COOKIE).and_then(|value| value.to_str().ok()) {
        request = request.header(header::COOKIE, cookie);
    }

    let response = request.send().await.map_err(|error| format!("Failed to check access: {error}"))?;
    if response.status() == StatusCode::UNAUTHORIZED {
        return Err("Authentication required".to_string());
    }
    if response.status() == StatusCode::FORBIDDEN {
        let reason = response.text().await.unwrap_or_else(|_| "Forbidden".to_string());
        return Ok(AccessCheckResponse { allowed: false, reason: Some(reason) });
    }
    if !response.status().is_success() {
        return Err(format!("Enterprise access check failed: {}", response.status()));
    }
    response.json().await.map_err(|error| format!("Invalid access decision payload: {error}"))
}

pub async fn send_query_audit(
    enterprise: &EnterpriseBridge,
    internal_token: Option<&str>,
    payload: &InternalQueryAuditRequest,
) -> Result<(), String> {
    let Some(token) = internal_token.filter(|value| !value.is_empty()) else {
        return Ok(());
    };

    let response = enterprise
        .client
        .post(format!("{}/api/v1/audit/internal/query", enterprise.base_url))
        .header("x-dbx-internal-token", token)
        .json(payload)
        .send()
        .await
        .map_err(|error| format!("Failed to submit query audit: {error}"))?;
    if !response.status().is_success() {
        return Err(format!("Enterprise query audit failed: {}", response.status()));
    }
    Ok(())
}

pub fn extract_cookie_value(headers: &HeaderMap, cookie_name: &str) -> Option<String> {
    let cookie_header = headers.get(header::COOKIE)?.to_str().ok()?;
    for pair in cookie_header.split(';') {
        let pair = pair.trim();
        let prefix = format!("{cookie_name}=");
        if let Some(value) = pair.strip_prefix(&prefix) {
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

pub fn extract_source_ip(headers: &HeaderMap, remote_addr: Option<std::net::SocketAddr>) -> Option<String> {
    if let Some(forwarded) = headers.get("x-forwarded-for").and_then(|value| value.to_str().ok()) {
        let source_ip = forwarded.split(',').next().unwrap_or_default().trim();
        if !source_ip.is_empty() {
            return Some(source_ip.to_string());
        }
    }
    remote_addr.map(|addr| addr.ip().to_string())
}

pub fn permission_for_request(method: &Method, path: &str) -> Option<&'static str> {
    if path == "/api/connection/list" {
        return Some("menu.connections.view");
    }
    if path == "/api/connection/save" || path == "/api/connection/test" {
        return Some("datasource.manage");
    }
    if path == "/api/connection/connect" || path == "/api/connection/disconnect" {
        return Some("datasource.connect");
    }
    if path.starts_with("/api/schema/") {
        return Some("datasource.browse");
    }
    if path.starts_with("/api/schema-diff/") {
        return Some("schema.diff");
    }
    if path == "/api/schema/cache-prefix" {
        return Some("datasource.browse");
    }
    if path.starts_with("/api/query/") {
        return Some("query.execute");
    }
    if path.starts_with("/api/data-compare/") {
        return Some("data.compare");
    }
    if path.starts_with("/api/transfer/") {
        return Some("transfer.execute");
    }
    if path.starts_with("/api/export/database") {
        return Some("export.database");
    }
    if path.starts_with("/api/export/query-result") {
        return Some("export.query");
    }
    if path.starts_with("/api/sql-file/") {
        return Some("sql_file.execute");
    }
    if path.starts_with("/api/history") {
        return Some("history.view");
    }
    if path.starts_with("/api/ai/") {
        return Some("ai.use");
    }
    if path.starts_with("/api/agents/") || path == "/api/plugins" {
        return Some("drivers.manage");
    }
    if path.starts_with("/api/layout/") || path.starts_with("/api/app-settings/") {
        return Some("settings.manage");
    }
    if path.starts_with("/api/saved-sql/") || path == "/api/saved-sql" {
        return Some("query.execute");
    }
    if path.starts_with("/api/import/") && method == Method::POST {
        return Some("query.execute");
    }
    if path == "/api/redis/list-databases" {
        return Some("datasource.browse");
    }
    if path.starts_with("/api/redis/") {
        return Some("query.execute");
    }
    if path == "/api/mongo/list-databases" || path == "/api/mongo/list-collections" {
        return Some("datasource.browse");
    }
    if path.starts_with("/api/mongo/") {
        return Some("query.execute");
    }
    None
}

pub fn build_access_check_requests(
    permission: &str,
    path: &str,
    query: Option<&str>,
    body: Option<&Value>,
) -> Vec<AccessCheckRequest> {
    let query_value = query.map(parse_query_value).unwrap_or(Value::Null);
    let empty_body = Value::Null;
    let body_value = body.unwrap_or(&empty_body);

    if path == "/api/transfer/start" {
        let requests = build_transfer_access_requests(permission, body_value, &query_value);
        if !requests.is_empty() {
            return requests;
        }
    }

    if path == "/api/data-compare/prepare-from-tables" {
        let requests = build_data_compare_access_requests(permission, body_value, &query_value);
        if !requests.is_empty() {
            return requests;
        }
    }

    vec![build_single_access_check_request(permission, body_value, &query_value)]
}

fn build_single_access_check_request(permission: &str, body: &Value, query: &Value) -> AccessCheckRequest {
    AccessCheckRequest {
        permission: permission.to_string(),
        datasource_id: find_string(body, &["connectionId", "connection_id", "id"])
            .or_else(|| find_string(query, &["connection_id", "connectionId"])),
        database: find_string(body, &["database", "db"]).or_else(|| find_string(query, &["database", "db"])),
        schema: find_string(body, &["schema"]).or_else(|| find_string(query, &["schema"])),
        table: find_string(body, &["table", "tableName", "name", "collection", "keyRaw", "key_raw"])
            .or_else(|| find_string(query, &["table", "collection"])),
    }
}

fn build_transfer_access_requests(permission: &str, body: &Value, query: &Value) -> Vec<AccessCheckRequest> {
    let source_connection_id =
        find_string(body, &["sourceConnectionId", "source_connection_id"]).or_else(|| {
            find_string(query, &["sourceConnectionId", "source_connection_id"])
        });
    let source_database =
        find_string(body, &["sourceDatabase", "source_database"]).or_else(|| {
            find_string(query, &["sourceDatabase", "source_database"])
        });
    let source_schema =
        find_string(body, &["sourceSchema", "source_schema"]).or_else(|| {
            find_string(query, &["sourceSchema", "source_schema"])
        });
    let target_connection_id =
        find_string(body, &["targetConnectionId", "target_connection_id"]).or_else(|| {
            find_string(query, &["targetConnectionId", "target_connection_id"])
        });
    let target_database =
        find_string(body, &["targetDatabase", "target_database"]).or_else(|| {
            find_string(query, &["targetDatabase", "target_database"])
        });
    let target_schema =
        find_string(body, &["targetSchema", "target_schema"]).or_else(|| {
            find_string(query, &["targetSchema", "target_schema"])
        });
    let tables = find_string_list(body, &["tables"]);

    let mut requests = Vec::new();
    if let Some(datasource_id) = source_connection_id {
        if tables.is_empty() {
            requests.push(AccessCheckRequest {
                permission: permission.to_string(),
                datasource_id: Some(datasource_id),
                database: source_database.clone(),
                schema: source_schema.clone(),
                table: None,
            });
        } else {
            for table in &tables {
                requests.push(AccessCheckRequest {
                    permission: permission.to_string(),
                    datasource_id: Some(datasource_id.clone()),
                    database: source_database.clone(),
                    schema: source_schema.clone(),
                    table: Some(table.clone()),
                });
            }
        }
    }

    if let Some(datasource_id) = target_connection_id {
        if tables.is_empty() {
            requests.push(AccessCheckRequest {
                permission: permission.to_string(),
                datasource_id: Some(datasource_id),
                database: target_database.clone(),
                schema: target_schema.clone(),
                table: None,
            });
        } else {
            for table in &tables {
                requests.push(AccessCheckRequest {
                    permission: permission.to_string(),
                    datasource_id: Some(datasource_id.clone()),
                    database: target_database.clone(),
                    schema: target_schema.clone(),
                    table: Some(table.clone()),
                });
            }
        }
    }

    requests
}

fn build_data_compare_access_requests(permission: &str, body: &Value, query: &Value) -> Vec<AccessCheckRequest> {
    let mut requests = Vec::new();

    if let Some(datasource_id) =
        find_string(body, &["sourceConnectionId", "source_connection_id"])
            .or_else(|| find_string(query, &["sourceConnectionId", "source_connection_id"]))
    {
        requests.push(AccessCheckRequest {
            permission: permission.to_string(),
            datasource_id: Some(datasource_id),
            database: find_string(body, &["sourceDatabase", "source_database"])
                .or_else(|| find_string(query, &["sourceDatabase", "source_database"])),
            schema: find_string(body, &["sourceSchema", "source_schema"])
                .or_else(|| find_string(query, &["sourceSchema", "source_schema"])),
            table: find_string(body, &["sourceTable", "source_table"])
                .or_else(|| find_string(query, &["sourceTable", "source_table"])),
        });
    }

    if let Some(datasource_id) =
        find_string(body, &["targetConnectionId", "target_connection_id"])
            .or_else(|| find_string(query, &["targetConnectionId", "target_connection_id"]))
    {
        requests.push(AccessCheckRequest {
            permission: permission.to_string(),
            datasource_id: Some(datasource_id),
            database: find_string(body, &["targetDatabase", "target_database"])
                .or_else(|| find_string(query, &["targetDatabase", "target_database"])),
            schema: find_string(body, &["targetSchema", "target_schema"])
                .or_else(|| find_string(query, &["targetSchema", "target_schema"])),
            table: find_string(body, &["targetTable", "target_table"])
                .or_else(|| find_string(query, &["targetTable", "target_table"])),
        });
    }

    requests
}

pub fn can_access_datasource(context: &AccessContext, datasource_id: &str) -> bool {
    if context.roles.iter().any(|role| role == "admin") {
        return true;
    }
    let mut allowed = false;
    for policy in matching_policies(context, datasource_id, None, None, None) {
        if policy.effect == "deny" {
            return false;
        }
        allowed = true;
    }
    allowed
}

pub fn can_access_scope(
    context: &AccessContext,
    datasource_id: &str,
    database: Option<&str>,
    schema: Option<&str>,
    table: Option<&str>,
) -> bool {
    if context.roles.iter().any(|role| role == "admin") {
        return true;
    }
    let mut allowed = false;
    for policy in matching_policies(context, datasource_id, database, schema, table) {
        if policy.effect == "deny" {
            return false;
        }
        allowed = true;
    }
    allowed
}

fn matching_policies<'a>(
    context: &'a AccessContext,
    datasource_id: &str,
    database: Option<&str>,
    schema: Option<&str>,
    table: Option<&str>,
) -> Vec<&'a AccessPolicy> {
    context
        .policies
        .iter()
        .filter(|policy| {
            policy.resource_type == "datasource"
                && (policy.resource_key == "*" || policy.resource_key == datasource_id)
                && matches_conditions(&policy.conditions, database, schema, table)
        })
        .collect()
}

fn matches_conditions(conditions: &Value, database: Option<&str>, schema: Option<&str>, table: Option<&str>) -> bool {
    let databases = string_list(conditions.get("databases"));
    if let Some(database) = database {
        if !databases.is_empty() && !databases.iter().any(|item| item == "*" || item == database) {
            return false;
        }
    }

    let schemas = string_list(conditions.get("schemas"));
    if let Some(schema) = schema {
        if !schemas.is_empty() {
            let mut candidates = vec![schema.to_string()];
            if let Some(database) = database {
                candidates.push(format!("{database}.{schema}"));
            }
            if !schemas.iter().any(|item| item == "*" || candidates.iter().any(|candidate| candidate == item)) {
                return false;
            }
        }
    }

    let tables = string_list(conditions.get("tables"));
    if let Some(table) = table {
        if !tables.is_empty() {
            let mut candidates = vec![table.to_string()];
            if let Some(schema) = schema {
                candidates.push(format!("{schema}.{table}"));
            }
            if let Some(database) = database {
                if let Some(schema) = schema {
                    candidates.push(format!("{database}.{schema}.{table}"));
                }
            }
            if !tables.iter().any(|item| item == "*" || candidates.iter().any(|candidate| candidate == item)) {
                return false;
            }
        }
    }

    true
}

fn string_list(value: Option<&Value>) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(|item| item.trim().to_string())
                .filter(|item| !item.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

fn parse_query_value(query: &str) -> Value {
    let entries = url::form_urlencoded::parse(query.as_bytes())
        .map(|(key, value)| (key.to_string(), Value::String(value.to_string())))
        .collect();
    Value::Object(entries)
}

fn find_string(value: &Value, keys: &[&str]) -> Option<String> {
    match value {
        Value::Object(map) => {
            for key in keys {
                if let Some(found) = map.get(*key).and_then(value_as_string) {
                    return Some(found);
                }
            }
            for nested in map.values() {
                if let Some(found) = find_string(nested, keys) {
                    return Some(found);
                }
            }
            None
        }
        Value::Array(items) => items.iter().find_map(|item| find_string(item, keys)),
        _ => None,
    }
}

fn find_string_list(value: &Value, keys: &[&str]) -> Vec<String> {
    match value {
        Value::Object(map) => {
            for key in keys {
                if let Some(found) = map.get(*key).map(value_as_string_list).filter(|items| !items.is_empty()) {
                    return found;
                }
            }
            for nested in map.values() {
                let found = find_string_list(nested, keys);
                if !found.is_empty() {
                    return found;
                }
            }
            Vec::new()
        }
        Value::Array(items) => items
            .iter()
            .filter_map(value_as_string)
            .collect(),
        _ => Vec::new(),
    }
}

fn value_as_string(value: &Value) -> Option<String> {
    match value {
        Value::String(text) if !text.trim().is_empty() => Some(text.trim().to_string()),
        Value::Number(number) => Some(number.to_string()),
        Value::Bool(boolean) => Some(boolean.to_string()),
        _ => None,
    }
}

fn value_as_string_list(value: &Value) -> Vec<String> {
    match value {
        Value::Array(items) => items.iter().filter_map(value_as_string).collect(),
        _ => value_as_string(value).into_iter().collect(),
    }
}

async fn proxy_with_client(client: &Client, base_url: &str, request: Request<Body>) -> Result<Response, String> {
    let (parts, body) = request.into_parts();
    let path_and_query = parts
        .uri
        .path_and_query()
        .map(|value| value.as_str().to_string())
        .unwrap_or_else(|| parts.uri.path().to_string());
    let upstream_url = format!("{base_url}{path_and_query}");
    let body_bytes = to_bytes(body, MAX_PROXY_BODY_BYTES)
        .await
        .map_err(|error| format!("Failed to read proxy request body: {error}"))?;

    let mut upstream = client.request(parts.method.clone(), upstream_url);
    for (name, value) in &parts.headers {
        if name == header::HOST || name == header::CONTENT_LENGTH {
            continue;
        }
        upstream = upstream.header(name, value);
    }
    let upstream_response =
        upstream.body(body_bytes.clone()).send().await.map_err(|error| format!("Enterprise proxy request failed: {error}"))?;

    let status = upstream_response.status();
    let headers = upstream_response.headers().clone();
    let response_bytes = upstream_response
        .bytes()
        .await
        .map_err(|error| format!("Failed to read enterprise proxy response: {error}"))?;

    let mut response = Response::builder().status(status);
    for (name, value) in &headers {
        if name == header::CONTENT_LENGTH {
            continue;
        }
        response = response.header(name, value);
    }

    response
        .body(Body::from(response_bytes))
        .map_err(|error| format!("Failed to build proxy response: {error}"))
}
