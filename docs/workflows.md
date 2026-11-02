# TATIP AI workflows

## 1. Code review and deployment

Code change -> Pull request to main -> GitHub syntax checks -> Review and merge
-> Cloud Build trigger -> Build container and deploy tatip-backend
-> Check Cloud Build/Cloud Run -> Run the smoke check below.

The repository now runs .github/workflows/validate.yml for pull requests and
updates to main. It checks Python, the main JavaScript file, and inline
JavaScript in HTML pages. It does not deploy by itself.

Deployment uses the existing cloudbuild.yaml. To deploy automatically after a
merge, configure one Cloud Build trigger in Google Cloud:

- Source repository: wiphaphonpw/tatip-backend
- Branch filter: ^main$
- Configuration file: cloudbuild.yaml

If a trigger already exists for main, keep using it; do not add a second one,
or each push can deploy twice. The Cloud Build service account needs permission
to push to Artifact Registry and deploy to Cloud Run. Check the exact service
account and grant access at the narrowest practical scope.

After deployment, check that the latest build succeeded and that the
tatip-backend service in us-central1 is serving the latest revision.

## 2. Chat and history use

Sign in -> Receive session token -> Ask a question -> AI returns an answer
-> Save user and AI messages -> Open History -> Load messages using the
session token -> Show question and answer together.

1. Sign in. After a deployment that adds session tokens, sign out and sign in
   again so the browser stores a fresh token.
2. Ask one test question in chat.
3. In browser Developer Tools > Network, check that api/save-chat-history
   succeeds.
4. Open History and confirm that api/history/<user_id> succeeds and the
   question and answer appear together.
5. In Firestore database customers, collection messages, confirm there are
   two records for the turn: SENDER is user and ai. In conversations,
   confirm LAST_MESSAGE and LAST_UPDATE changed.

Never share a screenshot that exposes an access_token. If the History request
returns 401, sign in again. If it returns 403, check that the logged-in
account matches the requested user ID.

