# Account verification and support

All medicine-category products require a prescription image. Customer signup requires a one-use email OTP. Password recovery uses a confirmed account email; existing accounts can link an email in account settings using an OTP and their current password.

Driver accounts remain owner-created. Arabic or English usernames and password confirmation are supported. Deletion disables the account and sessions while preserving wallets and orders. Active orders must be reassigned first. Ratings require a delivered order and its actual customer or driver.

## Runtime configuration

Set these in the existing Railway wellah service, keeping secrets out of Git:

- For Resend over HTTPS: RESEND_API_KEY and WALLAHA_EMAIL_FROM (a verified sender)
- Or use the following SMTP settings instead:
- WALLAHA_SMTP_HOST and WALLAHA_SMTP_FROM
- WALLAHA_SMTP_PORT: 587 STARTTLS or 465 TLS
- WALLAHA_SMTP_USER and WALLAHA_SMTP_PASSWORD when authentication is required
- OPENAI_API_KEY for optional AI support
- WALLAHA_AI_MODEL optionally overrides the default support model

OTP expires in 10 minutes and allows 5 attempts. Sending is limited by email and source IP. Without mail configuration, new registrations are rejected instead of bypassing verification. Existing logins continue working. Tests mock email delivery; they do not send real mail.

AI support is explicitly selected by the customer/driver. Unavailable AI falls back to a durable owner support ticket. Only the typed support message is sent to the AI service; account records, prescription images, OTPs, wallets and locations are not included. It cannot change orders or accounts. Browser notifications work while the dashboard is open; offline push is not implemented.

WhatsApp support uses the existing number in dashboard settings. The shortcut opens WhatsApp without automatically sending a message.

## Validation

Run `python -m unittest discover -s tests -v`. Tests cover OTP enforcement, single use, expiration, persistent attempt limits, password reset, account email linking, medicine validation, driver permissions, reversible deletion, active orders, rating ownership, support reply ownership and HTTPS email success/failure. JavaScript syntax and DOM rendering of every admin panel were also checked.

## Mobile OTP
Customer registration and password recovery now request SMS codes for Egyptian mobile numbers. Configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and either TWILIO_MESSAGING_SERVICE_SID or TWILIO_SMS_FROM in server environment. Enable Egypt delivery on the provider account. Keys must never be placed in browser files. Until configured, SMS requests fail closed. Local codes expire in ten minutes and allow five attempts. Existing email verification/linking endpoints remain compatible.

## Protected wallets and chat archive
Owner wallet access requires the existing administrator password and is session-scoped for five minutes. Every monetary mutation requires that password again. Settlements retain all original orders, messages, adjustments, and before/after audit entries; they mark paid/received balances instead of deleting history. Each wallet belongs to a stable driver account ID, and an owner can associate the driver's unique email with the account. Driver API responses exclude commission fields and audit details. Completed/cancelled customer orders are omitted from the customer feed and their chat becomes owner-only, while a minimal unrated completion prompt preserves the rating feature. The admin chat archive retrieves all assignment threads and supports paging.
