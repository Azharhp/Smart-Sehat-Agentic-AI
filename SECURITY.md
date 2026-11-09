# Security policy

This alpha prototype is for fictional data on localhost. It does not provide production authentication, verified clinician identity, hardened multi-tenant authorization or validated medical safety. Do not deploy it for real patient care.

## Reporting privately

Do not post vulnerabilities, credentials or patient information in public issues. If the repository Security tab offers **Report a vulnerability**, use GitHub private vulnerability reporting. If that option is absent, ask the maintainer for a private contact channel without publishing exploit details. A private contact and response SLA have not yet been established; no response time is promised.

Relevant reports include authentication or authorization bypass, exported or exposed records, checkpoint/media encryption errors, secret disclosure, unsafe file handling, injection across model/tool boundaries, and bypasses of confirmation or emergency guards. Include a minimal synthetic reproduction, affected commit and impact. Never attach real records or active credentials.

## Supported versions and boundaries

Development HEAD is the current review target; there is no production-supported release. Provider calls can transmit submitted content externally when explicitly enabled. Readable exports, screenshots and backups require access controls. Encryption keys must remain local and separate from publication. Clinical interpretation concerns should also be reported privately when they expose a reproducible unsafe behavior, but software fixes do not establish clinical validation.
