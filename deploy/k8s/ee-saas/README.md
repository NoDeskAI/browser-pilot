# Browser Pilot EE SaaS split deployment

This deployment shape keeps frontend and backend rollouts independent.

## Images

- Backend image: build with `Dockerfile.backend`.
- Frontend image: build with `Dockerfile.frontend`.
- Do not use the combined root `Dockerfile` for SaaS production rollouts unless a one-image fallback is explicitly needed.

## Rollout policy

- Frontend-only changes: build and push only the frontend image, then update only `browser-pilot-ee-saas-browser-pilot-frontend`.
- Backend-only changes: build and push only the backend image, then update only `browser-pilot-ee-saas-browser-pilot-backend`.
- Full-stack changes: roll backend and frontend separately, with backend health verified before switching frontend when API contracts changed.

## Routing

The public Ingress routes:

- `/api/*` to the backend service.
- `/healthz` and `/readyz` to the backend service.
- `/*` to the frontend service.

This keeps SPA refresh and deep links served by Nginx while API and WebSocket traffic stay on FastAPI.
