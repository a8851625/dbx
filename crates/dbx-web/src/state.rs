use dbx_core::connection::AppState;
use reqwest::Client;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{broadcast, Mutex, RwLock};

pub struct LoginRateLimit {
    pub fail_count: u32,
    pub locked_until: Option<std::time::Instant>,
}

#[derive(Clone)]
pub struct ExportDownload {
    pub path: PathBuf,
    pub download_name: String,
}

#[derive(Clone)]
pub struct EnterpriseBridge {
    pub base_url: String,
    pub session_cookie_name: String,
    pub client: Client,
}

pub struct WebState {
    pub app: Arc<AppState>,
    pub data_dir: PathBuf,
    pub password_hash: RwLock<Option<String>>,
    pub sessions: RwLock<HashSet<String>>,
    pub sse_channels: RwLock<HashMap<String, broadcast::Sender<String>>>,
    pub export_downloads: RwLock<HashMap<String, ExportDownload>>,
    pub login_rate_limit: Mutex<LoginRateLimit>,
    pub enterprise: Option<EnterpriseBridge>,
    pub internal_service_token: Option<String>,
}

impl WebState {
    pub async fn remove_sse_channel(&self, id: &str) {
        self.sse_channels.write().await.remove(id);
    }
}
