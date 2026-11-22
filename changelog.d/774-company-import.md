Added

* **A new company from an exported file (issue #774).** `POST /v1/companies/import` creates a company from a file made by the export, owned by the person importing it, with its agents, business units and routines. Each value passes the same check as its own route, and what fails is listed rather than half applied. A file never sets what runs on the machine or what an Owner sets on purpose (stack commands, research hosts, sandbox, console producers, protected paths, forge policy, integration review; the autonomous level becomes assisted): those are listed for the Owner to set. Routines arrive turned off, and provider connections are listed to set up with their keys. The backend records its own digest of the file's content beside the file's claimed one, and whether they match; an edited file still imports.

Fixed

* **The company export now carries each routine's variables (issue #774).** Without them a routine whose description used `{{name}}` could not be imported again.
