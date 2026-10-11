# Repository workflow

The user grants standing permission for normal Git operations for this project,
including commit, branch, merge, pull and push to `comache/jai-lighting`.
Do not ask for permission for each routine Git operation. Git CLI transport is
authorized to preserve existing local commit identities and history. Prefer the
connected GitHub plugin for GitHub API operations and remote verification.

Preserve existing released tags, including the annotated `v0.1.0` tag, unless
the user explicitly requests a change. The validated v0.2 implementation has
been merged into `main`; v0.1.0 is preserved through its existing tag.

Do not create GitHub Releases, change repository visibility or deploy to Home
Assistant unless explicitly requested. Stop and report unexpected divergence or
conflicts before destructive resolution; standing permission covers normal Git
operations, not unrequested history rewriting.
