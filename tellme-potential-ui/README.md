# tellme-potential-ui

A Next.js frontend for the existing TeLLMe and TraceFix workflow. It provides a
chat-based interface for submitting smart-room questions and displaying
privacy-safe aggregate answers.

The browser communicates with server-side Next.js API routes. Those routes proxy
requests to a separately running TeLLMe/TraceFix backend, coordinate verification
and synthesis, and shape the response before it reaches the UI.

## What The UI Does

- Lets a user ask a smart-room question in natural language.
- Supports LLM/API processing with deterministic mode as a fallback.
- Shows staged progress while TeLLMe and TraceFix process the request.
- Displays aggregate answers, verification status, and sensor attribution.
- Withholds raw captures, identities, source paths, and internal backend payloads.
- Provides conversation, appearance, response, and export controls.

## Project Structure

- `app/page.tsx` - main conversation and guidelines workspace.
- `components/result-view.tsx` - answer, sensors used, and evidence tabs.
- `components/guidelines-panel.tsx` - generated guideline view and JSON copy action.
- `lib/api/types.ts` - frontend/backend data contract.
- `lib/api/client.ts` - coordinates the query, verification, synthesis, and answer requests.
- `app/api/tellme` - server-side proxy routes for the TeLLMe/TraceFix workflow.
- `lib/api/server/runner.ts` - shared backend address and request helper.

## Backend Integration

The browser calls same-origin routes under `/api/tellme`. The Next.js server then
contacts the backend address configured by `TELLME_BACKEND_URL`. Keeping this
address server-side avoids coupling browser code to a developer's local backend
port and provides one place for response validation and privacy filtering.

The default address is:

```text
http://127.0.0.1:8788
```

Each developer can override it in `.env.local` without changing tracked source
files. API keys are entered through the UI at runtime and must not be added to
`.env.example` or committed to Git.

## Getting Started

1. From the CityOS repository root, start the compatible TeLLMe/TraceFix
   backend in one PowerShell terminal:

```powershell
.\local-ui\start-runner.ps1
```

2. In a second PowerShell terminal, enter this frontend directory and create its
   local environment file:

```powershell
Set-Location .\tellme-potential-ui
Copy-Item .env.example .env.local
```

3. If necessary, edit `.env.local` so its address matches the running backend:

```dotenv
TELLME_BACKEND_URL=http://127.0.0.1:8788
```

4. Install dependencies and start the frontend:

```bash
npm ci
npm run dev
```

On Windows PowerShell, if script execution policy blocks `npm`, run:

```powershell
npm.cmd run dev
```

Open http://127.0.0.1:3000. Restart the frontend whenever `.env.local` changes.

The frontend and backend ports are independent. By default, Next.js runs on port
`3000` while the CityOS TeLLMe/TraceFix runner uses port `8788`.

## Validation

Production build:

```powershell
npm.cmd run build
```

Current note: `npm run lint` is present in `package.json`, but ESLint is not
currently installed in `devDependencies`, so linting needs a tooling follow-up
before it will run successfully.

## Built With

This repository was bootstrapped with v0 and uses Next.js, React, Tailwind CSS, shadcn-style components, and lucide-react icons.

Continue working in v0:

https://v0.app/chat/projects/prj_VJ3X9ekWjesnaRFfslf8ADLHQDAP
