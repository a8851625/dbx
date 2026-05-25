use axum::body::{to_bytes, Body};
use std::sync::Arc;

use argon2::password_hash::rand_core::OsRng;
use argon2::password_hash::SaltString;
use argon2::{Argon2, PasswordHash, PasswordHasher, PasswordVerifier};
use axum::extract::State;
use axum::http::{header, Request, StatusCode};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::enterprise;
use crate::state::WebState;

#[derive(Deserialize)]
pub struct LoginRequest {
    pub password: String,
}

#[derive(Deserialize)]
pub struct ChangePasswordRequest {
    pub old_password: String,
    pub new_password: String,
}

#[derive(Serialize)]
pub struct AuthCheckResponse {
    pub authenticated: bool,
    pub required: bool,
    pub setup_required: bool,
}

const MAX_ATTEMPTS: u32 = 5;
const LOCKOUT_SECS: u64 = 60;

pub async fn login(State(state): State<Arc<WebState>>, Json(body): Json<LoginRequest>) -> Result<Response, StatusCode> {
    let hash_guard = state.password_hash.read().await;
    let hash_str = match hash_guard.as_deref() {
        Some(h) => h.to_string(),
        None => {
            return Ok((StatusCode::OK, Json(serde_json::json!({"ok": true}))).into_response());
        }
    };
    drop(hash_guard);

    // Check rate limit
    {
        let rl = state.login_rate_limit.lock().await;
        if let Some(locked_until) = rl.locked_until {
            if locked_until > std::time::Instant::now() {
                let remaining = (locked_until - std::time::Instant::now()).as_secs();
                return Ok((
                    StatusCode::TOO_MANY_REQUESTS,
                    Json(serde_json::json!({"error": format!("请 {remaining} 秒后再试")})),
                )
                    .into_response());
            }
        }
    }

    let parsed_hash = PasswordHash::new(&hash_str).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    if Argon2::default().verify_password(body.password.as_bytes(), &parsed_hash).is_err() {
        let mut rl = state.login_rate_limit.lock().await;
        rl.fail_count += 1;
        if rl.fail_count >= MAX_ATTEMPTS {
            rl.locked_until = Some(std::time::Instant::now() + std::time::Duration::from_secs(LOCKOUT_SECS));
            rl.fail_count = 0;
        }
        return Err(StatusCode::UNAUTHORIZED);
    }

    // Success — reset rate limit
    {
        let mut rl = state.login_rate_limit.lock().await;
        rl.fail_count = 0;
        rl.locked_until = None;
    }

    let token = uuid::Uuid::new_v4().to_string();
    state.sessions.write().await.insert(token.clone());

    let cookie = format!("dbx_session={token}; Path=/; HttpOnly; SameSite=Lax");
    Ok((StatusCode::OK, [("set-cookie", cookie.as_str())], Json(serde_json::json!({"ok": true}))).into_response())
}

pub async fn setup(State(state): State<Arc<WebState>>, Json(body): Json<LoginRequest>) -> Result<Response, StatusCode> {
    // Only allow setup when no password is configured
    if state.password_hash.read().await.is_some() {
        return Err(StatusCode::FORBIDDEN);
    }

    if body.password.is_empty() {
        return Err(StatusCode::BAD_REQUEST);
    }

    let salt = SaltString::generate(&mut OsRng);
    let hash = Argon2::default()
        .hash_password(body.password.as_bytes(), &salt)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .to_string();

    // Save to database
    state.app.storage.save_password_hash(&hash).await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;

    // Update in-memory state
    *state.password_hash.write().await = Some(hash);

    // Auto-login: create session
    let token = uuid::Uuid::new_v4().to_string();
    state.sessions.write().await.insert(token.clone());

    let cookie = format!("dbx_session={token}; Path=/; HttpOnly; SameSite=Lax");
    Ok((StatusCode::OK, [("set-cookie", cookie.as_str())], Json(serde_json::json!({"ok": true}))).into_response())
}

pub async fn check(State(state): State<Arc<WebState>>, req: Request<axum::body::Body>) -> Json<AuthCheckResponse> {
    let has_password = state.password_hash.read().await.is_some();
    if !has_password {
        return Json(AuthCheckResponse { authenticated: false, required: false, setup_required: true });
    }
    let authenticated = match extract_session_token(&req) {
        Some(token) => state.sessions.read().await.contains(&token),
        None => false,
    };
    Json(AuthCheckResponse { authenticated, required: true, setup_required: false })
}

pub async fn change_password(
    State(state): State<Arc<WebState>>,
    Json(body): Json<ChangePasswordRequest>,
) -> Result<Response, StatusCode> {
    let hash_guard = state.password_hash.read().await;
    let hash_str = match hash_guard.as_deref() {
        Some(h) => h.to_string(),
        None => return Err(StatusCode::BAD_REQUEST),
    };
    drop(hash_guard);

    if body.new_password.is_empty() {
        return Err(StatusCode::BAD_REQUEST);
    }

    let parsed_hash = PasswordHash::new(&hash_str).map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    if Argon2::default().verify_password(body.old_password.as_bytes(), &parsed_hash).is_err() {
        return Err(StatusCode::UNAUTHORIZED);
    }

    let salt = SaltString::generate(&mut OsRng);
    let new_hash = Argon2::default()
        .hash_password(body.new_password.as_bytes(), &salt)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
        .to_string();

    state.app.storage.save_password_hash(&new_hash).await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    *state.password_hash.write().await = Some(new_hash);

    Ok((StatusCode::OK, Json(serde_json::json!({"ok": true}))).into_response())
}

pub async fn logout(State(state): State<Arc<WebState>>, req: Request<axum::body::Body>) -> Response {
    if let Some(token) = extract_session_token(&req) {
        state.sessions.write().await.remove(&token);
    }
    let cookie = "dbx_session=; Path=/; HttpOnly; Max-Age=0";
    (StatusCode::OK, [("set-cookie", cookie)], Json(serde_json::json!({"ok": true}))).into_response()
}

fn extract_session_token<B>(req: &Request<B>) -> Option<String> {
    let cookie_header = req.headers().get("cookie")?.to_str().ok()?;
    for pair in cookie_header.split(';') {
        let pair = pair.trim();
        if let Some(value) = pair.strip_prefix("dbx_session=") {
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

fn has_valid_internal_token<B>(req: &Request<B>, state: &WebState) -> bool {
    let Some(expected) = state.internal_service_token.as_deref() else {
        return false;
    };
    req.headers()
        .get("x-dbx-internal-token")
        .and_then(|value| value.to_str().ok())
        .map(|value| value == expected)
        .unwrap_or(false)
}

pub async fn auth_middleware(
    State(state): State<Arc<WebState>>,
    req: Request<axum::body::Body>,
    next: Next,
) -> Response {
    if req.uri().path().starts_with("/api/internal/") {
        if has_valid_internal_token(&req, &state) {
            return next.run(req).await;
        }
        return StatusCode::UNAUTHORIZED.into_response();
    }

    if let Some(enterprise_bridge) = state.enterprise.as_ref() {
        let path = req.uri().path().to_string();
        if path.starts_with("/api/auth/") {
            return next.run(req).await;
        }
        if !path.starts_with("/api/") {
            return next.run(req).await;
        }

        let (parts, body) = req.into_parts();
        let should_parse_body = parts
            .headers
            .get(axum::http::header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(|value| value.contains("application/json"))
            .unwrap_or(false);
        let query = parts.uri.query().map(str::to_string);
        let method = parts.method.clone();

        let (body_value, req) = if should_parse_body {
            let body_bytes = match to_bytes(body, 2 * 1024 * 1024).await {
                Ok(bytes) => bytes,
                Err(_) => return StatusCode::BAD_REQUEST.into_response(),
            };
            let body_value = serde_json::from_slice::<Value>(&body_bytes).ok();
            let request = Request::from_parts(parts, Body::from(body_bytes));
            (body_value, request)
        } else {
            (None, Request::from_parts(parts, body))
        };

        let permission = match enterprise::permission_for_request(&method, &path) {
            Some(permission) => permission,
            None => {
            let mut headers = req.headers().clone();
            headers.remove(header::COOKIE);
            if let Some(cookie) = req.headers().get(header::COOKIE) {
                headers.insert(header::COOKIE, cookie.clone());
            }
            match enterprise::fetch_access_context(enterprise_bridge, &headers).await {
                Ok(_) => return next.run(req).await,
                Err(reason) if reason == "Authentication required" => {
                    return StatusCode::UNAUTHORIZED.into_response();
                }
                    Err(_) => return StatusCode::BAD_GATEWAY.into_response(),
                }
            }
        };

        let payloads =
            enterprise::build_access_check_requests(permission, &path, query.as_deref(), body_value.as_ref());

        for payload in &payloads {
            match enterprise::check_access(enterprise_bridge, req.headers(), payload).await {
                Ok(result) if result.allowed => {}
                Ok(result) => {
                    return (StatusCode::FORBIDDEN, result.reason.unwrap_or_else(|| "Forbidden".to_string()))
                        .into_response();
                }
                Err(reason) if reason == "Authentication required" => {
                    return StatusCode::UNAUTHORIZED.into_response();
                }
                Err(_) => return StatusCode::BAD_GATEWAY.into_response(),
            }
        }

        return next.run(req).await;
    }

    // No password set — allow everything
    if state.password_hash.read().await.is_none() {
        return next.run(req).await;
    }

    // Auth endpoints are always accessible
    let path = req.uri().path();
    if path.starts_with("/api/auth/") {
        return next.run(req).await;
    }

    // Non-API requests (static files) are always accessible
    if !path.starts_with("/api/") {
        return next.run(req).await;
    }

    // Check session token
    if let Some(token) = extract_session_token(&req) {
        if state.sessions.read().await.contains(&token) {
            return next.run(req).await;
        }
    }

    StatusCode::UNAUTHORIZED.into_response()
}
