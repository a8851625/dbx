# DBX OIDC Local And Shared Test Handoff

This branch adds a compose-native OIDC path so DBX can be validated against a real identity provider instead of the long-running mock mode.

## Local Compose Contract

`deploy/docker-compose.yml` now starts:

- `dbx` on `http://localhost:4224`
- `postgres` on `localhost:5432`
- `keycloak` on `http://localhost:8080`

The imported local realm contract is:

- Realm: `dbx`
- Client ID: `dbx-web`
- Client Secret: `dbx-web-secret`
- Login user: `dbx-admin`
- Password: `dbx-admin-123`
- Email: `admin@example.com`

Start and verify the stack with:

```bash
docker compose -f deploy/docker-compose.yml up --build
python3 deploy/scripts/oidc_compose_smoke.py
```

Manual browser validation:

1. Open `http://localhost:4224/login`
2. Click the enterprise login button
3. Sign in to Keycloak with `dbx-admin / dbx-admin-123`
4. Confirm DBX returns to `/` with an authenticated session

## Shared Test Environment Variables

For shared test, keep the same DBX auth contract but make the endpoint split explicit:

- Browser-facing URLs:
  - `OIDC_AUTHORIZE_URL`
  - `OIDC_LOGOUT_URL`
  - `OIDC_REDIRECT_URI`
  - `OIDC_POST_LOGOUT_REDIRECT_URI`
- Backend-facing URLs:
  - `OIDC_TOKEN_URL`
  - `OIDC_USERINFO_URL`
- Core flags:
  - `OIDC_ENABLED=true`
  - `OIDC_MOCK_MODE=false`
  - `OIDC_PROVIDER_NAME`
  - `OIDC_CLIENT_ID`
  - `OIDC_CLIENT_SECRET`
  - `OIDC_ALLOWED_EMAIL_DOMAINS` when the test IdP should be restricted

`OIDC_AUTHORIZE_URL` and `OIDC_LOGOUT_URL` must be reachable by the browser.
`OIDC_TOKEN_URL` and `OIDC_USERINFO_URL` only need to be reachable from the DBX backend container, so they can use an internal service DNS name.

## Shared Test Handoff Checklist

- Push a deployment branch named `test/myt-42-oidc-shared-test-prep`
- Record the branch, commit SHA, and deployed DBX URL
- Record the IdP issuer URL, login URL, and test credentials
- Run `python3 deploy/scripts/oidc_compose_smoke.py --app-base-url <dbx-url> --issuer <issuer-url>`
- Attach the smoke output and a browser login screenshot to the issue or deployment evidence
- Confirm QA has the IdP login account and the post-login landing page expectation (`/`)
