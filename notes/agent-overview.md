# What the repo-bug-hunter agent does

The agent in `repo-bug-hunter` is an autonomous bug-fixer for Python repositories. It gets a real GitHub issue and the repository's code. It looks around the code, edits it, runs tests, and submits a fix as a diff. The project's own hidden tests then grade that fix. The point of the project is to **measure** how well the agent fixes bugs, and to test whether a change helps, especially making it write a failing test first.

## Steps for one bug

**1. Set up an isolated sandbox** ([run.py:119](../repo_bug_hunter/run.py#L119), [env.py](../repo_bug_hunter/env.py))
- It starts a Docker container from that task's SWE-bench image, with the repo at `/testbed` and its dependencies installed.
- The network is switched off ([env.py:38](../repo_bug_hunter/env.py#L38)), so the agent can't install packages or look up the real fix.
- It records the repo's starting state ([tools.py:253](../repo_bug_hunter/tools.py#L253)), so the final diff contains only the agent's own changes.

**2. Give the model its instructions** ([agent.py:27](../repo_bug_hunter/agent.py#L27))
- **System prompt:** "You are an autonomous software engineer… fix the underlying behavior, not just the example… nobody will answer questions… call submit when done."
- **User message:** the issue text inside `<issue>` tags, plus the repo name.

**3. Loop: think, then act, up to 50 steps** ([agent.py:104](../repo_bug_hunter/agent.py#L104))

Each step:
1. It sends the whole conversation to the model ([agent.py:106](../repo_bug_hunter/agent.py#L106)).
2. The model replies with tool calls. It has six tools ([tools.py:53](../repo_bug_hunter/tools.py#L53)):
   - `bash`: run commands and tests
   - `read_file`: read up to 400 lines at a time
   - `search`: grep the code
   - `edit_file`: replace one exact snippet
   - `write_file`: create or overwrite a file
   - `submit`: hand in the fix
3. It runs each tool call in the container ([agent.py:145](../repo_bug_hunter/agent.py#L145)) and sends the output back to the model as the next message. Long output is cut to 10,000 characters.
4. If the model answers in text without calling a tool, it gets a nudge: "nothing ran, use the tools" ([agent.py:133](../repo_bug_hunter/agent.py#L133)).

In practice the model usually explores the code, reproduces the bug, edits the source, re-runs the tests, and then submits.

**4. Stop** when any of these happens:
- The model calls `submit` (normal finish)
- 50 steps are used up (`step_limit`)
- The cost reaches $2 (`cost_limit`)
- The API fails (`api_error`) or the model refuses (`refusal`)

**5. Produce the patch** ([tools.py:266](../repo_bug_hunter/tools.py#L266))
- It takes a `git diff` of everything the agent changed.
- Test files and repro scripts are left out, because the grader discards changes to tests.
- Junk such as `.pyc` files and plots is also left out.
- The full record of every step is saved to `runs/<name>/trajs/<id>.json`: thinking, tool calls, outputs, tokens and cost.

## The "test-first" variant

With `--variant test_first`, the agent gets one extra rule and one extra tool ([agent.py:40](../repo_bug_hunter/agent.py#L40)):
1. **Source files are locked** at the start ([tools.py:135](../repo_bug_hunter/tools.py#L135)). Only test or repro files can be written.
2. It writes a script that reproduces the bug, such as `repro_test.py`, and registers it with `record_failing_test` ([tools.py:200](../repo_bug_hunter/tools.py#L200)). The harness runs it, and it must **fail** on the current code.
   - Once it fails, source files unlock.
   - After 3 attempts that don't fail, they unlock anyway.
3. At `submit`, the harness runs the repro again ([tools.py:224](../repo_bug_hunter/tools.py#L224)), and it must **pass**. If it still fails, the first submit is rejected and the agent has to keep working.

## Around the agent

```
repo-bug-hunter run       → the agent attempts N sampled SWE-bench Verified tasks (in parallel) → preds.jsonl
repo-bug-hunter evaluate  → the official SWE-bench grader applies each patch and runs the maintainers' hidden tests
repo-bug-hunter analyze   → resolve rate, cost, steps, failure categories, baseline vs test-first statistics
repo-bug-hunter viewer    → a static web page that replays every run step by step
repo-bug-hunter pr        → the same agent on fresh GitHub PRs outside SWE-bench (bugs the model can't have seen)
```

The model can be a free OpenRouter model (the default), Claude, or a local Ollama model. Only the way the model is called changes; the loop and the tools stay the same.
