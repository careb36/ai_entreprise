Fixed

* **The app names the company, not "Company <id>".** `GET /v1/auth/me` sent each membership without the company's name, so after a sign-in the company selector, the company picker and a switch from a notification all showed the placeholder "Company <id>". Each membership now carries `company_name`, the app uses it in those three places, and a placeholder kept in the browser from before is replaced by the name on the next load.
