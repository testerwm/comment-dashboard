# Comment Dashboard UI Rules

When modifying this project, treat it as a polished product dashboard for crawler jobs, JSON file management, and comment analysis.

## Visual Direction

- Use a modern product-dashboard style inspired by Screenlane/Mobbin examples: clear workspace hierarchy, dense but readable data surfaces, refined cards, confident spacing, and visible interaction states.
- Prefer useful operational layouts over marketing-style sections. This is a working tool, not a landing page.
- Right-side pages must feel designed, not merely functional: use section headers, compact metadata, list/table-like rows, selected states, disabled states, and clear primary/secondary actions.
- Avoid generic “four metric cards plus charts” unless the metrics directly support the active workflow.

## Layout

- Keep the left nav stable and compact.
- Main content should use a clear page shell: top summary, primary workspace, secondary panels.
- For JSON files, use a file-management list with selection, bulk actions, file metadata, and safe destructive actions.
- For dashboards, tie metrics to the selected video/post whenever possible. Global totals should be visibly secondary.
- For comment data, prioritize readable threads, high-like comments, source links, and per-item context.

## Components

- Buttons need distinct hierarchy: primary, secondary, danger, disabled.
- Data rows need hover, selected, and checkbox states.
- Cards should use subtle borders, shadows, and section dividers. Keep border-radius at 8px or less.
- Avoid nested cards unless needed for repeated list items or threaded comments.
- Do not add decorative blobs, oversized gradients, or purely ornamental hero content.

## Content

- Labels should be specific and operational: “批量删除”, “加载到表盘”, “导出原始 JSON”.
- Avoid unclear sample buttons or dead controls. If a button exists, it must work.
- Empty states should explain what the user can do next.

## Verification

After UI changes:

- Run `node --check static/app.js`.
- Run `python3 -m py_compile server.py` if server code changed.
- Open `http://127.0.0.1:8787` and verify the changed page with Playwright or browser screenshots.
- Check desktop width around 1440px and ensure text does not overlap or overflow.
