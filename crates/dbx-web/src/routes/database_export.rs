use std::path::{Path as StdPath, PathBuf};
use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::sse::{Event, Sse};
use axum::response::IntoResponse;
use axum::Json;
use dbx_core::database_export::{self, DatabaseExportRequest, ExportProgress, ExportStatus};
use futures::stream::Stream;
use serde::Deserialize;

use crate::error::AppError;
use crate::state::{ExportDownload, WebState};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartExportRequest {
    pub request: DatabaseExportRequest,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelExportRequest {
    pub export_id: String,
}

pub async fn start_database_export(
    State(state): State<Arc<WebState>>,
    Json(body): Json<StartExportRequest>,
) -> Result<Json<serde_json::Value>, AppError> {
    let mut req = body.request;
    let export_id = req.export_id.clone();
    let output_path = resolve_export_output_path(&state, &req.file_path, &export_id);
    if let Some(parent) = output_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| AppError(format!("Failed to prepare export directory: {e}")))?;
    }
    let download_name = output_path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .unwrap_or("database-export.sql")
        .to_string();
    req.file_path = output_path.to_string_lossy().into_owned();

    {
        let mut downloads = state.export_downloads.write().await;
        downloads.remove(&export_id);
    }

    let (tx, _) = tokio::sync::broadcast::channel::<String>(256);
    state.sse_channels.write().await.insert(export_id.clone(), tx.clone());

    let app = state.app.clone();
    let state_clone = state.clone();

    tokio::spawn(async move {
        let result = database_export::export_database_sql_core(&app, &req, |progress| {
            if let Ok(json) = serde_json::to_string(&progress) {
                let _ = tx.send(json);
            }
        })
        .await;

        match result {
            Ok(()) => {
                state_clone.export_downloads.write().await.insert(
                    req.export_id.clone(),
                    ExportDownload { path: output_path, download_name },
                );
            }
            Err(e) => {
                let progress = ExportProgress {
                    export_id: req.export_id.clone(),
                    current_object: String::new(),
                    object_index: 0,
                    total_objects: 0,
                    rows_exported: 0,
                    total_rows: None,
                    status: ExportStatus::Error,
                    error: Some(e),
                };
                if let Ok(json) = serde_json::to_string(&progress) {
                    let _ = tx.send(json);
                }
                let _ = tokio::fs::remove_file(&output_path).await;
            }
        }

        database_export::clear_export_cancelled(&req.export_id).await;
        state_clone.remove_sse_channel(&req.export_id).await;
    });

    Ok(Json(serde_json::json!({ "exportId": export_id })))
}

pub async fn database_export_progress(
    State(state): State<Arc<WebState>>,
    Path(export_id): Path<String>,
) -> Result<Sse<impl Stream<Item = Result<Event, std::convert::Infallible>>>, AppError> {
    let channels = state.sse_channels.read().await;
    let tx = channels.get(&export_id).ok_or_else(|| AppError("Export not found".to_string()))?;
    let rx = tx.subscribe();
    drop(channels);
    Ok(crate::sse::sse_from_channel(rx))
}

fn resolve_export_output_path(state: &WebState, requested_path: &str, export_id: &str) -> PathBuf {
    let requested = requested_path.trim();
    if requested.starts_with("__web_export_") {
        return state.data_dir.join("exports").join(format!("{export_id}.sql"));
    }
    let path = PathBuf::from(requested);
    if path.is_absolute() {
        path
    } else {
        state.data_dir.join(path)
    }
}

fn is_safe_export_download_path(state: &WebState, path: &StdPath) -> bool {
    if let Ok(canonical) = std::fs::canonicalize(path) {
        let export_dir = state.data_dir.join("exports");
        if let Ok(canonical_export_dir) = std::fs::canonicalize(&export_dir) {
            return canonical.starts_with(canonical_export_dir);
        }
    }
    false
}

fn escape_content_disposition_filename(file_name: &str) -> String {
    file_name.replace('\\', "\\\\").replace('"', "\\\"")
}

pub async fn download_database_export(
    State(state): State<Arc<WebState>>,
    Path(export_id): Path<String>,
) -> Result<impl IntoResponse, AppError> {
    let download = {
        let downloads = state.export_downloads.read().await;
        downloads.get(&export_id).cloned()
    }
    .ok_or_else(|| AppError("Export file not found".to_string()))?;

    if !is_safe_export_download_path(&state, &download.path) {
        state.export_downloads.write().await.remove(&export_id);
        return Err(AppError("Invalid export file path".to_string()));
    }

    let bytes = tokio::fs::read(&download.path).await.map_err(|e| AppError(e.to_string()))?;
    state.export_downloads.write().await.remove(&export_id);
    let _ = tokio::fs::remove_file(&download.path).await;
    let content_disposition = HeaderValue::from_str(&format!(
        "attachment; filename=\"{}\"",
        escape_content_disposition_filename(&download.download_name)
    ))
    .map_err(|e| AppError(format!("Invalid export download filename: {e}")))?;

    Ok((
        StatusCode::OK,
        [
            (header::CONTENT_TYPE, HeaderValue::from_static("application/sql; charset=utf-8")),
            (header::CONTENT_DISPOSITION, content_disposition),
        ],
        bytes,
    ))
}

pub async fn cancel_database_export(
    State(state): State<Arc<WebState>>,
    Json(req): Json<CancelExportRequest>,
) -> Json<serde_json::Value> {
    database_export::set_export_cancelled(&req.export_id).await;
    state.export_downloads.write().await.remove(&req.export_id);
    Json(serde_json::json!({ "cancelled": true }))
}

