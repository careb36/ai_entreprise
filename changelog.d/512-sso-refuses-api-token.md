Security

* **SSO refuses an API token (issue #512).** `GET /v1/auth/sso/{key}/start` and `POST /v1/auth/sso/exchange` answer 401 "API tokens cannot be used to sign in with SSO" to a request whose `Authorization` header carries an API token (`Bearer fab_…`), valid or not: a token is for scripts and never signs a person in.
