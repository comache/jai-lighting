# Repository workflow

Use the connected GitHub plugin for GitHub operations. Do not switch to GitHub
CLI, Git push, direct HTTP, or SSH for remote GitHub operations without an
explicit exception from the user. If the plugin lacks a required capability,
report that limitation instead of silently changing interfaces.

Preserve the v0.1.0 integration behavior and architecture while creating the
baseline. Do not start v0.2 work without an explicit request from the user.
