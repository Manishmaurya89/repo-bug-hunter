# Security

## Reporting a vulnerability

Please don't open a public issue. Report it privately instead, from the repository's **Security** tab, with **Report a vulnerability**.

Only the latest release on PyPI gets fixes.

## How the tool keeps your machine and keys safe

- **Commands a model wrote run in a sandbox.** For SWE-bench tasks and the smoke test, they run in a Docker container with no network and a process limit, and their output and file reads are capped.
- **`smoke --local` is the exception.** It runs the toy task in a temporary folder on your machine, with environment variables that look like credentials removed. Use it only for the toy task.
- **API keys stay out of the agent's reach.** They never enter the container and are never written to run results.
- **Saved keys are private.** `repo-bug-hunter setup` stores keys in `~/.config/repo-bug-hunter/config.json`, readable only by your user, and `setup --forget` deletes the file. Keys are typed without echo or read from standard input, never from the command line. On a shared machine, prefer environment variables or a secret manager.
- **`repo-bug-hunter pr` runs third-party code in Docker.** It builds an image of the project under test, which runs that project's install scripts inside the build container.
- **Releases come from CI.** The GitHub workflows pin every action to a commit and give each job only the permissions it needs. PyPI releases use trusted publishing, so no upload token is stored anywhere.
