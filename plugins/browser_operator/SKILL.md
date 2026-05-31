---
name: browser-operator
description: Use the Hermes Browser Operator extension to inspect a live browser page, find UI elements, prepare drafts, fill forms, verify login state, and queue user-confirmed browser actions when no official API is available.
---

# Hermes Browser Operator

Use this skill when the user wants Hermes to help with a live web page through the browser instead of an official API: drafting comments, filling publishing forms, navigating an interface, checking that the right account is signed in, or preparing a post for final human review.

## Operating model

The Chrome extension is the page-side companion. It sends visible page snapshots to the Browser Operator bridge and polls for queued actions. Each open tab is tracked by a stable `tab_id` such as `chrome-tab:123`, so Hermes can keep working with the intended page even if the user switches browser tabs. Hermes tools read the latest snapshot, reason about the page, queue a small action for a specific tab, then inspect the result.

Default startup:

```bash
hermes plugins enable browser_operator
hermes browser-operator serve
```

Load the unpacked extension from:

```bash
hermes browser-operator extension-path
```

## Safety rules

- Do not bypass CAPTCHA, 2FA, paywalls, anti-bot systems, bank/payment flows, or site restrictions.
- Treat send, publish, delete, purchase, settings changes, and account changes as high-risk actions.
- For high-risk actions, queue only a user-confirmed action and explain what will happen.
- Prefer draft mode: fill text and stop before final submit/publish.
- Do not request or reveal passwords, cookies, access tokens, refresh tokens, or session secrets.
- Use the user's existing browser session as the primary auth method.
- If a session has expired, ask the user to sign in or complete 2FA manually.

## Tool workflow

1. Call `browser_operator_list_tabs` or `browser_operator_find_tab` when multiple tabs may be open.
2. Pass the chosen `tab_id` to `browser_operator_latest_snapshot` to inspect the intended page.
3. Call `browser_operator_auth_status` with the same `tab_id` if account state matters.
4. Call `browser_operator_find_elements` with a semantic target such as `comment field`, `title input`, `publish button`, or `account menu`.
5. If content is below or above the viewport, call `browser_operator_scroll_page` with the same `tab_id`.
6. For browser history or URL navigation, call `browser_operator_navigate_page` with the same `tab_id`.
7. If the target is clear, call `browser_operator_queue_action` with the same `tab_id`.
8. Poll `browser_operator_action_result` for the returned action id.
9. Re-read `browser_operator_latest_snapshot` after scrolling, navigation, or form changes.

Snapshots include `regions`, which are larger page/card/article chunks. Use them to verify feed posts, Threads items, YouTube Studio rows, or other content where individual buttons do not carry enough context.

## Action guidance

- `highlight`: safe first step when confidence is low.
- `scroll`: use `browser_operator_scroll_page` for page movement; no confirmation needed.
- `fill`: acceptable for drafts and form fields.
- `select`: acceptable for dropdown choices.
- `click`: requires confirmation for risky buttons.
- `navigate`: use `browser_operator_navigate_page`; URL changes require confirmation by default.
- `back`, `forward`, `reload`: use `browser_operator_navigate_page` for browser navigation controls.
- `auth_probe`: use when you need the extension to refresh login/account signals.
- `focus_tab`: use through `browser_operator_queue_action` when the correct tab should be brought to the front.

## Login/session handling

Use `browser_operator_auth_status` before starting a workflow that requires an account. It is a heuristic. If the expected account is not visible, ask the user to check the page.

Recommended modes:

- `session_only`: use the already signed-in browser profile.
- `assisted_login`: open the login page and let the user enter credentials manually.
- `vault_login`: reserved for trusted future flows where secrets are injected by a protected secret manager and never shown to the LLM.

The first implementation supports `session_only` and `assisted_login` patterns. Do not invent password automation unless the user has explicitly configured a protected vault flow.
