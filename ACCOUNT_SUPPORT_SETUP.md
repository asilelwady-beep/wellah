# Account verification and support

All medicine-category products require a prescription image. Customer signup requires a one-use email OTP. Password recovery uses a confirmed account email; existing accounts can link an email in account settings using an OTP and their current password.

Driver accounts remain owner-created. Arabic or English usernames and password confirmation are supported. Deletion disables the account and sessions while preserving wallets and orders. Active orders must be reassigned first. Ratings require a delivered order and its actual customer or driver.

## Runtime configuration

Set these in the existing Railway wellah service, keeping secrets out of Git:

- WALLAHA_SMTP_HOST and WALLAHA_SMTP_FROM
- WALLAHA_SMTP_PORT: 587 STARTTLS or 465 TLS
- WALLAHA_SMTP_USER and WALLAHA_SMTP_PASSWORD when authentication is required
- OPENAI_API_KEY for optional AI support
- WALLAHA_AI_MODEL optionally overrides the default support model

OTP expires in 10 minutes and allows 5 attempts. Sending is limited by email and source IP. Without mail configuration, new registrations are rejected instead of bypassing verification. Existing logins continue working. Tests mock email delivery; they do not send real mail.

AI support is explicitly selected by the customer/driver. Unavailable AI falls back to a durable owner support ticket. Only the typed support message is sent to the AI service; account records, prescription images, OTPs, wallets and locations are not included. It cannot change orders or accounts. Browser notifications work while the dashboard is open; offline push is not implemented.

WhatsApp support uses the existing number in dashboard settings. The shortcut opens WhatsApp without automatically sending a message.

## Validation

Run `python -m unittest discover -s tests -v`. The 11 tests cover OTP enforcement, single use, expiration, persistent attempt limits, password reset, account email linking, medicine validation, driver permissions, reversible deletion, active orders, rating ownership and support reply ownership. JavaScript syntax and DOM rendering of every admin panel were also checked.
