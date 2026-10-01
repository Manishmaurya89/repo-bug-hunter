# Learning guide: understand repo-bug-hunter from zero

This guide explains every term used in this project in simple words. Each idea comes with an everyday example and with where it appears in **our** project, so you learn by looking at real things you built.

**How to read it:** go in order. Each part builds on the one before. Don't try to learn everything in one day. One part per day is a good pace. At the end there are hands-on exercises you can run on your Mac, and interview questions with answers.

| Part | What you'll learn |
|---|---|
| [1. The whole project as a story](#part-1-the-whole-project-as-a-story) | The big idea, with no technical words |
| [2. Code basics](#part-2-code-basics) | Repository, issue, git, diff, test, exit code… |
| [3. Docker](#part-3-docker) | Image, container, why we need them |
| [4. AI models](#part-4-ai-models) | LLM, token, context window, API, tool calling… |
| [5. Agents](#part-5-agents) | Agent loop, tools, trajectory, harness |
| [6. SWE-bench](#part-6-swe-bench-the-exam) | The exam our agent takes, and how it's graded |
| [7. The experiment](#part-7-the-experiment) | Baseline, variant, test-first, seed |
| [8. What we measure](#part-8-what-we-measure) | Resolve rate, cost, steps, failure categories |
| [9. Statistics without fear](#part-9-statistics-without-fear) | Noise, confidence interval, p-value |
| [10. Other engineering words](#part-10-other-engineering-words) | Timeout, cache, smoke test, static site… |
| [11. Our two real runs, step by step](#part-11-our-two-real-runs-step-by-step) | Everything above, in action |
| [12. Hands-on exercises](#part-12-hands-on-exercises) | Things to try on your Mac |
| [13. Explaining it in an interview](#part-13-explaining-it-in-an-interview) | A short pitch and common questions |
| [14. What to learn next](#part-14-what-to-learn-next) | Where to go deeper |

---

## Part 1: The whole project as a story

Imagine you hire a **new intern** as a software developer. You give them a real bug report from a real project. They read the code, change something, and say "done".

**How do you know they really fixed it?** The project's own team already fixed this bug in the past and wrote **tests** that check the fix. You keep those tests hidden from the intern. When the intern says "done", you run the hidden tests on their work. If the tests pass, the bug is fixed. If not, it isn't.

Now replace the intern with an **AI model**. That is repo-bug-hunter:

1. We give the AI a real bug report.
2. The AI looks through the code, edits files and runs commands, one small action at a time.
3. When it says "done", the official grader runs the hidden tests.
4. We count how many bugs it fixed, how much it cost, and how many actions it took.

**The experiment.** We also want to know: *does one good habit make the AI better?* The habit is to **write a small test that shows the bug before trying to fix it**, just like a doctor confirming a diagnosis before giving medicine. We run the AI twice on the same bugs, once without the habit and once with it (we force the habit), and compare.

**The honesty part.** With only a few bugs, one version can win just by luck. So we use statistics to say whether a difference is real or could be luck.

**The demo.** Every run is recorded, and a website replays it step by step, like watching a cricket match highlight ball by ball.

That's the whole project. Everything below explains the words we use to build it.

---

## Part 2: Code basics

**Repository (repo)**: a project's folder with all its code and the full history of changes.
*In our project:* the bug we tested is in the `psf/requests` repository, the code of the popular Python library `requests`.

**GitHub**: a website where people store repositories and work on them together.

**Issue**: a bug report or request posted on GitHub. Anyone can write one.
*In our project:* the issue we used says:
> "requests.get is ALWAYS sending content length … For example http://amazon.com returns 503 for every get request that contains 'content-length' header."

**Bug / fix**: a bug is code doing the wrong thing. A fix is the change that makes it right.

**Library (package)**: code written by others that you can reuse. `requests` is a library for talking to websites from Python.

**Dependency**: a library your project needs in order to work.
*In our project:* `anthropic`, `openai`, `datasets` and `swebench` (listed in [pyproject.toml](pyproject.toml)).

**uv**: a tool that installs a project's dependencies and runs commands with them. `uv sync` installs everything. `uv run <command>` runs a command with the project's packages available.

**Virtual environment (`.venv`)**: a private box of packages for one project only, so projects don't disturb each other. `uv` creates it for you.

**Terminal / shell / bash**: the text window where you type commands. `bash` is one common shell program.

**Git**: a tool that tracks every change to files, like "track changes" in Word, but for a whole folder.

**Commit**: a saved snapshot of the files at one moment, with a message saying what changed.

**Diff / patch**: text that shows exactly what changed between two versions. Lines starting with `-` were removed and lines starting with `+` were added. The **patch** is what our agent hands in as its answer. [Part 11](#part-11-our-two-real-runs-step-by-step) teaches you to read one line by line.

**Test**: a small program that checks whether code does the right thing. Here is a real test from our smoke test ([repo_bug_hunter/smoke.py](repo_bug_hunter/smoke.py)):
```python
def test_mean():
    assert mean([1, 2, 3]) == 2
```
**`assert`** means "this must be true, or the test fails". If `mean()` returns anything other than 2, the test **fails**. Otherwise it **passes**.

**pytest**: a tool that finds all the tests in a project, runs them, and shows which pass and which fail.

**Exit code**: a number every program gives back when it finishes. The terminal uses it to know whether things went well.

| Exit code | Meaning | Where it matters in our project |
|---|---|---|
| `0` | Success | A reproduction test that **passes** on buggy code proves nothing, so it's rejected. |
| `1` (or any non-zero) | Something failed | A reproduction test that **fails** on buggy code proves the bug exists, so it's accepted. |
| `124` | Stopped because of a timeout | The command ran too long. |
| `126` / `127` | Couldn't run at all (for example, the command doesn't exist) | Rejected, because it proves nothing about the bug. |

Try it: in the terminal, `ls; echo $?` prints `0`, and `no_such_command; echo $?` prints `127`.

**Regular expression (regex)**: a pattern for searching text. `Content-Length` finds that exact text. `def (get|post)` finds `def get` or `def post`.

**grep**: a command that searches files for a regex. Our `search` tool uses it.

**JSON**: a common text format for data, like `{"tool": "bash", "command": "ls"}`. We save every run as JSON.

---

## Part 3: Docker

**The problem.** Each bug in SWE-bench comes from an old version of a project, which needs an exact old version of Python and old library versions. Installing all of that on your Mac for 500 different bugs would be a nightmare, and "it works on my machine" wouldn't mean it works for the grader.

**Docker** solves this by packing a complete, ready-to-use computer setup into one file.

**Image**: a frozen, ready-made setup, like a sealed tiffin box packed with exactly the right food. It never changes.
*In our project:* each bug has its own image. For our task it's `swebench/sweb.eval.x86_64.psf_1776_requests-1142`, which is 3.8 GB.

**Container**: a running copy of an image. You can start many containers from one image, and throw them away after. What happens inside a container doesn't change the image. It's like opening a *copy* of the tiffin box: you can eat from it, and the original stays sealed.
*In our project:* each task gets a fresh container. The agent works inside it, and the container is deleted afterwards.

**Docker Desktop**: the app that runs Docker on your Mac. It must be running before you start a run.

**x86-64 vs ARM, and Rosetta**: computer chips speak different "machine languages". Your Mac's M-series chip speaks **ARM**. SWE-bench images are built for Intel/AMD chips, which speak **x86-64**. **Rosetta** is Apple's translator that lets your Mac run x86-64 programs. It works, but it's slower.

**`/testbed`**: the folder inside the container where the bug's repository lives.

**No network (`--network none`)**: our containers have no internet. That way the agent can't install packages to hide problems, and it can't look up the real fix online (that would be cheating).

**conda**: another package manager. SWE-bench images use it inside the container. Our code switches it on before every command, the same way the grader does.

**Docker without installing it**: GitHub lends you machines that already have Docker. **GitHub Actions** runs jobs on them, free and unlimited for public repositories. **Codespaces** gives you one as VS Code in the browser, with 60 free hours a month.
*In our project:* [.github/workflows/experiment.yml](.github/workflows/experiment.yml) runs each task in its own Actions job, so anyone can run the whole experiment from a fork without installing anything. These machines are x86-64, so there's no Rosetta slowdown either.

---

## Part 4: AI models

**LLM (Large Language Model)**: a program trained on a huge amount of text. It predicts what text should come next, which lets it answer questions, write code and follow instructions.

**Model**: one specific LLM. *In our project:* the default is `poolside/laguna-s-2.1:free` (free, in the cloud through OpenRouter). The others are `gemma4` (free, runs on your Mac) and Claude models such as `claude-opus-5-5` (paid, in the cloud).

**Parameters / "8B"**: the numbers a model learned during training. "8B" means 8 billion of them. Bigger models are usually smarter but need more memory. With 16 GB of RAM, about 8B is the practical limit on your Mac.

**Prompt**: the text you send to the model.

**System prompt**: standing instructions sent with every request, like the rules of the job. *Ours begins:* "You are an autonomous software engineer fixing an issue in a Python repository…" (in [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)).

**Token**: a small piece of text. In English, a word is roughly 1 to 2 tokens, and code uses more. Models read and write tokens, and prices and limits are counted in tokens.
*In our project:* the baseline run used 33k tokens and the test-first run used 197k.

**Context window**: how many tokens the model can see at once, its "working memory" or "desk size". If the conversation grows bigger than the desk, the oldest pages fall off.
*In our project:* Ollama gives models only 4,096 tokens by default, which is far too small. We created `gemma4-32k` with 32,768 tokens. When even that fills up, our code removes the oldest tool outputs, six at a time.

**API (Application Programming Interface)**: a way for one program to talk to another by sending requests in a fixed format. It's like ordering at a restaurant counter: you give your order in the expected format, and you get your food back.

**API key**: a secret password that tells the service who you are, so it can count or bill your use. Treat it like a password: never commit it to git or paste it into a chat, and put it in an environment variable or a GitHub secret.
*In our project:* a free OpenRouter key in `OPENROUTER_API_KEY`. Our code even hides keys from the agent's commands, because their output goes to the model.

**Endpoint / base URL**: the address where the API lives. *Ours:* `https://openrouter.ai/api/v1`, or `http://localhost:11434/v1` for Ollama. **localhost** means "this same computer", your Mac.

**OpenRouter**: one API, and one key, for hundreds of models from many companies. Some models are **free** (their names end in `:free`).
*In our project:* the default provider, chosen in [repo_bug_hunter/providers.py](repo_bug_hunter/providers.py), so anyone can run the agent without paying.

**Rate limit**: a cap on how many requests you may send, like a canteen that serves only so many plates per minute. Free OpenRouter models allow 20 requests a minute and 50 a day, or 1,000 a day after buying $10 of credits once. A busy model can also refuse for a while (HTTP error **429**, "too many requests").
*In our project:* the code waits out short limits. When the **daily** limit is used up, it stops cleanly, and you run the same command the next day to continue.

**Ollama**: a free app that runs models on your own computer. **Local model** means the model runs on your Mac. A **cloud model** runs on the company's servers.

**OpenAI-compatible API**: a common "language" for talking to models. OpenAI designed it, and many other tools (Ollama, LM Studio, vLLM) speak it too. Because of this, one piece of our code works with all of them.

**Adapter**: a translator between two formats, like a travel plug adapter.
*In our project:* [repo_bug_hunter/openai_compat.py](repo_bug_hunter/openai_compat.py) translates between Claude's message format, which our loop uses, and the OpenAI format, which OpenRouter and Ollama use.

**Tool calling (function calling)**: a model can't run commands by itself. It can only write text. With tool calling, the model writes a structured request such as:
```json
{"tool": "read_file", "input": {"path": "requests/models.py"}}
```
Our program sees it, actually reads the file, and sends the content back to the model. **Only models trained for this can do it.** That's why `gemma4` works and `llama3` doesn't.

**Reasoning / thinking**: notes the model writes to itself before it acts. We save them, so you can read *why* it did something.

**Hallucination**: the model confidently invents something untrue. For example, a small model sometimes writes a tool call as plain text (so nothing runs) and then *imagines* the result. Our loop catches this and tells it: "No tool was called, so nothing ran."

**Refusal / fallback (Claude only)**: sometimes a safety filter blocks a reply. That's a refusal. With **fallback** switched on, the request is automatically retried on another Claude model.

**Prompt caching (Claude only)**: every step sends the whole conversation again. Caching lets the server reuse the part it has already processed, which makes it cheaper and faster. It's like using a bookmark instead of re-reading a book from page 1.

**Effort (Claude only)**: a setting for how hard the model thinks before answering. Higher effort gives better answers at a higher cost.

---

## Part 5: Agents

**Chatbot vs agent**: a chatbot answers once. An **agent** *takes actions* in a loop until the job is done: it looks at files, edits them, runs tests, looks at the results, and tries again.

**The agent loop** (in [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py)):
```
   ┌──────────────────────────────────────────┐
   │ 1. THINK: the model reads everything so far│
   │ 2. ACT:   it asks for a tool call        │
   │ 3. RUN:   our code runs the tool         │
   │ 4. SEE:   the result goes back to model  │
   └────────────── repeat ────────────────────┘
          until it calls "submit" or hits a limit
```

**Tool**: one action the agent is allowed to take. Ours:

| Tool | In plain words |
|---|---|
| `bash` | Run any terminal command, for example run the tests |
| `read_file` | Open a file and read it, with line numbers |
| `search` | Find text in all files (uses grep) |
| `edit_file` | Replace one exact piece of text in a file |
| `write_file` | Create a new file or replace a whole file |
| `submit` | "I'm done, this is my answer" |
| `record_failing_test` | (test-first only) "Here is my test that shows the bug" |

**Step (turn)**: one round of the loop. *Our baseline run took 9 steps and the test-first run took 26.*

**Trajectory**: the complete recording of a run: every step, thought, tool call, output, token count and cost. It's like a flight's black box. *Saved in* `runs/<name>/trajs/<task>.json`.

**Limits**: so an agent can't run forever, we stop it after 50 steps (`--max-steps`) or $2 of cost (`--max-cost`).

**Exit status**: why a run ended.

| Status | Meaning |
|---|---|
| `submitted` | The agent called submit |
| `step_limit` | It used all its steps |
| `cost_limit` | It spent its money limit |
| `refusal` | The model refused |
| `api_error` | The model server failed |
| `env_error` | Docker failed |

**Harness**: the code *around* the model that controls it. It runs the tools, checks the arguments and enforces the rules. The word comes from a horse's harness: the horse gives the power, and the harness steers it. SWE-bench's grading program is also called a "harness" (the evaluation harness).

---

## Part 6: SWE-bench, the exam

**Benchmark**: a standard exam everyone uses, so that scores can be compared fairly, like a board exam for AI.

**SWE-bench** ("SWE" means software engineering): an exam made of real GitHub bugs from popular Python projects.

**SWE-bench Verified**: 500 of those bugs, checked by human reviewers to make sure each one is fair and solvable. They come from 12 projects. Django alone has 231 of them. We download it from **Hugging Face**, a website that hosts AI datasets and models.

**Task / instance / instance_id**: one bug in the exam. The ID `psf__requests-1142` means: owner `psf`, project `requests`, number `1142`.

**Difficulty**: human reviewers estimated how long each fix would take a developer. Of the 500 tasks:

| Label | Tasks |
|---|---|
| `<15 min fix` | 194 |
| `15 min - 1 hour` | 261 |
| `1-4 hours` | 42 |
| `>4 hours` | 3 |

`--difficulty "<15 min fix"` picks only the easiest ones, which is useful for a small free model.

**Gold patch**: the real fix the project's team wrote. It's the answer key, and the agent never sees it.

**Hidden tests**: the tests the team wrote along with the real fix. There are two groups:

- **FAIL_TO_PASS**: tests that *failed* before the fix and must *pass* after it. They prove the bug is fixed.
  *Our task:* `test_no_content_length`.
- **PASS_TO_PASS**: tests that *passed* before and must *still pass*. They prove nothing else broke.
  *Our task:* 5 tests, such as `test_basic_building`.

**Resolved**: every FAIL_TO_PASS test passes **and** every PASS_TO_PASS test still passes. Both conditions, no partial credit.

**Grader (evaluation harness)**: SWE-bench's official program. It takes the agent's patch, applies it in a *fresh* container, runs the hidden tests and writes a report. We don't write our own grading, because using the official one makes our numbers trustworthy.

**Modal**: a cloud service that can run the grading on remote computers if your Mac is too slow. It's optional and needs an account.

---

## Part 7: The experiment

**Hypothesis**: the idea you want to test. *Ours:* "If the agent must first write a test that shows the bug, it will fix more bugs."

**Baseline**: the normal version, used for comparison. **Variant**: the changed version. *Ours:* `baseline` and `test_first`.

**A/B test**: compare two versions where **only one thing is different**.

**Controlled variables**: everything else is kept exactly the same: the same bugs, model, prompt and limits. It's like testing a new fertilizer: two plants with the same soil, water and sunlight, and only the fertilizer differs. If the plant grows more, the fertilizer gets the credit.

**Seed**: a number that makes "random" choices repeatable. With `--seed 0 --n 50`, you get the *same* 50 bugs every time, so both variants face the same exam paper.

**TDD (test-driven development) / test-first**: a well-known habit among good developers:
1. **Red**: write a test that shows the bug. Run it, and it fails (red).
2. **Green**: fix the code. Run the test again, and it passes (green).

**Reproduction ("repro")**: a small test or script that makes the bug happen on purpose. It proves you understand the bug.

**The gate (lock)**: in the test-first variant, our harness **forces** the habit:
- Source files are **locked**, so the agent can't edit them.
- The agent writes a test and registers it with `record_failing_test`.
- Our harness runs that test on the unchanged code. **It must fail**, which proves the bug exists. Only then are the files unlocked.
- At `submit`, the harness runs the test again. **It must pass.** If it doesn't, the submit is rejected once.

**Why force it instead of just asking?** A model can ignore a request in the prompt. A lock can't be ignored. That's what makes it a real, measurable difference.

**Escape hatches**: some bugs are very hard to reproduce. After 3 failed attempts, the files unlock anyway. The second submit is always accepted. This way the rule never makes a task impossible, and we record when this happens.

---

## Part 8: What we measure

**Resolve rate** = bugs fixed ÷ bugs tried. Fixing 20 out of 50 gives 40%.

**Cost per issue**: average money spent per bug. It's always $0 with a local model, so we also show **tokens per issue**.

**Cost per resolved issue**: total money ÷ bugs fixed. It tells you what one *success* really costs.

**Steps per issue**: how many turns the agent needed.

**Failure categories**: for every bug *not* fixed, we record **why**. Each one gets exactly one reason, and we check them in this order:

| Category | Plain meaning | Everyday example |
|---|---|---|
| Run crashed | Docker or the model server broke | The exam hall lost power, not the student's fault |
| Gave up | Ran out of steps or money, refused, or handed in nothing | Left the answer sheet blank |
| Patch did not apply | The answer couldn't even be put into the code | Wrote the answer on the wrong page |
| Grader error | The grader couldn't produce results | The examiner lost the paper |
| **Wrong file** | Changed none of the files the real fix changed | A mechanic fixing the tyre when the brake was broken. **Our baseline run.** |
| Broke other tests | Fixed something but broke something else | Fixed the brake, and now the lights don't work |
| Right file, tests still fail | Right place, but the fix isn't correct | Worked on the brake, but it still doesn't stop |

---

## Part 9: Statistics without fear

### Why we need statistics at all

Flip a fair coin 10 times. You might get 7 heads. That doesn't mean the coin is unfair. **With few tries, luck plays a big part.** The same is true for our agent: with 50 bugs, one variant can "win" by a few bugs just by luck.

**Sample size (n)**: how many things you tested. The bigger n is, the less luck matters.

**Noise**: the random ups and downs that come from luck, not from a real difference.

### Confidence interval: "the true answer is probably in this range"

If the agent fixes 20 of 50 bugs, the rate is 40%. But on a *different* 50 bugs it might get 35% or 45%. A **95% confidence interval** gives a range that very likely contains the agent's *true* rate. These numbers come from our own code:

| Result | Rate | 95% confidence interval |
|---|---|---|
| 0 of 1 (our real baseline run) | 0% | 0% to 79% |
| 1 of 5 | 20% | 4% to 62% |
| 20 of 50 | 40% | 28% to 54% |

See how the range gets **narrower** as we test more bugs? With 1 task, the range is so wide that it tells us almost nothing.

**Wilson interval**: the specific formula we use for this range. It works well even with small numbers.

### Paired comparison: same exam paper for both

To compare two students fairly, give them **the same exam paper** and compare question by question. We do the same thing: both variants get the same bugs. For every bug there are four possibilities:

| | test-first solved | test-first failed |
|---|---|---|
| **baseline solved** | both | only baseline |
| **baseline failed** | only test-first | neither |

### McNemar's test: look only at the "flips"

Bugs that both solved, or that neither solved, tell us nothing about *which is better*. Only the **flips** matter, the bugs where exactly one variant succeeded. If test-first really made no difference, the flips should split about 50/50, like coin flips.

### p-value: "could this just be luck?"

The **p-value** answers: *"If there were really no difference, how likely is a result this uneven, just by luck?"*

- **Small p (below 0.05, which is 5%)**: unlikely to be luck. The difference is probably real.
- **Big p**: it could easily be luck. Don't claim an improvement yet.

Three examples, all calculated with our own code:

| Situation (50 bugs) | Baseline | Test-first | Flips | p-value | Verdict |
|---|---|---|---|---|---|
| Example A | 18 (36%) | 22 (44%) | only baseline 3, only test-first 7 | **0.34** | Could be luck: like getting 7 heads out of 10 flips |
| Example B | 17 (34%) | 27 (54%) | only baseline 2, only test-first 12 | **0.013** | Unlikely to be luck: test-first probably helps |
| **Our real run (1 bug)** | 0 | 1 | only test-first 1 | **1.0** | Tells us nothing yet: we need 20+ bugs |

Notice that Example A shows an 8-point improvement that *looks* good but isn't convincing. That's exactly the trap our report protects you from.

### Bootstrap: "what if we had drawn different bugs?"

We create 10,000 "pretend" versions of our bug list by picking bugs at random from the list we have (the same bug can be picked twice). We calculate the improvement for each version. The middle 95% of those results is the **bootstrap interval**.

*Example A:* the interval is −4 to +20 points. It **includes 0** ("no improvement"), which agrees with p = 0.34. We only show this interval with 20+ bugs, because with fewer it's misleading.

### One more thing: models are a bit random

Run the same model on the same bug twice, and it may act differently. The strongest evidence is to **repeat** each variant (for example `--name baseline_2`) and check that the difference holds.

---

## Part 10: Other engineering words

**Timeout**: a time limit for a command. If a test hangs forever, we stop it. Our timeouts run *inside* the container, so the test is really killed.

**Truncate**: cut long text short. Command output over 10,000 characters keeps only the first and last 5,000.

**Cache / `.pyc` / bytecode**: Python converts `.py` files into faster `.pyc` files and reuses them. The danger is that Python might reuse an *old* `.pyc` after a file changes (a **stale** cache), so old code runs. We delete the `.pyc` after every edit.

**Snapshot**: a record of how things looked at one moment. At the start of each task we take one (`git stash create`), so the patch only contains *the agent's* changes.

**Patch applies / `git apply`**: putting a patch into the code. If the patch is broken (for example, it contains junk files), it "doesn't apply".

**Unit tests**: small tests for each piece of *our own* code. We have 56, in [tests/](tests/). They need no Docker and no model.

**Fake model (mock)**: in our unit tests, we replace the real AI with a script that gives fixed answers. The tests are then fast, free and always give the same result.

**Smoke test**: a quick "does it switch on?" check. The name comes from electronics: turn it on and see whether smoke comes out. *Ours* ([repo_bug_hunter/smoke.py](repo_bug_hunter/smoke.py)) runs the agent on a tiny toy bug.

**Lint**: a tool that checks code for small mistakes and style problems without running it.

**Preflight check**: checks before starting, like a pilot's checklist. *Our* `run` checks that Docker and the model server are up before starting 50 tasks.

**Resume**: continue where you stopped. Re-running the same command skips finished bugs and retries the ones that failed because Docker or the model server broke.

**Workers / parallel**: how many bugs run at the same time. Use 1 with a local model, because the model itself is the bottleneck.

**Static website**: a website that is just files (HTML plus data), with no server running code. It can be hosted for free.

**GitHub Pages**: GitHub's free hosting for static websites. We'll put the replay site there.

**Deep link**: a link that opens a specific place inside a page, for example `…/#run=test_first&id=psf__requests-1142&step=10`.

**Headless Chrome**: the Chrome browser running without a window, controlled by a program. We used it to take screenshots and check that the replay page looks right on desktop and phone.

**Markdown (`.md`)**: simple text formatting. `# Title`, `**bold**` and `| tables |` become nice pages on GitHub. This guide is Markdown.

---

## Part 11: Our two real runs, step by step

This is where everything comes together. Both runs used the same bug and the same free model (`gemma4-32k`). The only difference was the test-first rule.

### The bug, in simple words

When a program asks a website for a page, that's a **GET request**. It sends small labels called **headers** with the request. The **Content-Length** header says how much data you are sending. A GET request normally sends *no* data, so this header isn't needed. But `requests` *always* added `Content-Length: 0`, and some servers, like Amazon's, rejected such requests with error 503.

### The real fix (gold patch): learn to read a diff

```diff
     def prepare_content_length(self, body):
-        self.headers['Content-Length'] = '0'
         if hasattr(body, 'seek') and hasattr(body, 'tell'):
             body.seek(0, 2)
             self.headers['Content-Length'] = str(body.tell())
             body.seek(0, 0)
         elif body is not None:
             self.headers['Content-Length'] = str(len(body))
+        elif self.method not in ('GET', 'HEAD'):
+            self.headers['Content-Length'] = '0'
```

Reading it line by line:
- `def prepare_content_length(self, body):` is the function that decides the Content-Length header. It's in `requests/models.py`.
- The `-` line was **removed**. It always set the header to `'0'` at the start, for every request. That was the bug.
- Lines without `+` or `-` are unchanged. If the body is a file, the code measures its size. If there is some other body, the code uses its length.
- The two `+` lines were **added**: "if there's no body, set `'0'` only when the method is *not* GET or HEAD."

So after the fix, a GET request with no data sends no Content-Length header.

### Run 1: baseline (no test-first rule), not resolved

| Step | What the agent did | What it means |
|---|---|---|
| 1 | `bash: ls -F` | Looked at the project's files |
| 2 | `bash: ls -F requests/` | Looked inside the library folder |
| 3 | `read_file: requests/api.py` | Opened `api.py`. **Wrong guess**: the header is added in `models.py` |
| 4–5 | `edit_file` ×2 | Both failed: the text it wanted to replace didn't match the file exactly |
| 6 | `read_file` lines 47–56 | Looked more closely at `get()` |
| 7 | `edit_file` | Succeeded: made `get()` throw away `data`, `files` and `json` arguments |
| 8 | `bash: pytest` | Ran the tests: 21 failed, 5 passed |
| 9 | `submit` | Handed in the answer |

**What went wrong:** it never *searched* for "Content-Length", so it never found the real cause. At step 8, 21 tests failed, but those 21 fail in this environment even without any change. Only the 5 PASS_TO_PASS tests are expected to pass. The agent couldn't tell which failures were its fault, so it submitted anyway.

**Grader's verdict:** `test_no_content_length` still failed, and the 5 other tests still passed. The result was **not resolved**, and our analysis put it in the category **"Wrong file"**.

### Run 2: test-first (rule enforced), resolved ✅

| Step | What the agent did | What it means |
|---|---|---|
| 1 | `search` for "content-length" in path `.*` | Error: `.*` isn't a real folder. (Today's fix makes sure the model is told this is an error.) |
| 2 | *(no tool call)* | Wrote text instead of acting, so our loop reminded it |
| 3–5 | `ls`, `search`, `ls requests/` | Explored the project |
| 6 | `read_file: requests/models.py` | Opened the **right** file this time |
| 7 | `edit_file` on models.py | **🔒 BLOCKED**: "Source files are locked until you register a failing reproduction" |
| 8 | *(no tool call)* | Reminded again |
| 9 | `write_file: repro_test.py` | Wrote a test: "build a GET request and check that it has no Content-Length header" |
| 10 | `record_failing_test: python /testbed/repro_test.py` | Our harness ran it on the original code, and it **failed** ("Found unexpected 'Content-Length' header: 0"). The bug was proven, so the files **unlocked** |
| 11 | `edit_file` | Failed (text didn't match) |
| 12 | `edit_file` | **Succeeded:** replaced the "always set 0" line with "if there's no body, stop here" |
| 13–25 | More edits, re-reading the file, 3 turns with no tool call | It tried to change more, but all 6 of these edits failed because the text didn't match. Luckily the fix from step 12 was already done |
| 26 | `submit` | Our harness re-ran `repro_test.py`. It **passed**, so the submit was accepted |

**The agent's patch:**
```diff
     def prepare_content_length(self, body):
-        self.headers['Content-Length'] = '0'
+        if body is None:
+            return
```
In words: "if there's no body, don't add any Content-Length header."

**Grader's verdict:** `test_no_content_length` now passes, and all 5 other tests still pass. The result was **resolved**.

### What we learn from these two runs

1. **The rule worked as designed.** It blocked the early edit, made the agent prove the bug, and checked the fix at submit.
2. **"Resolved" means "passes the hidden tests", not "identical to the real fix".** The agent's fix is simpler. It also stops sending `Content-Length: 0` for a POST with no body, which the real fix still does. The hidden tests don't check that case. Good engineers notice this kind of thing.
3. **One bug proves nothing.** In run 2, the agent opened `models.py` *before* it wrote its test. Was the rule the reason for its success, or did the model just explore differently this time, since models are a bit random? **With one bug, we can't know.** That's exactly why the experiment needs 50 bugs and statistics (p = 1.0 here).
4. **Small models struggle with exact edits.** 7 of the test-first run's edits failed because the text didn't match exactly. That's a weakness to watch for in bigger runs.
5. **It cost more effort.** Test-first took 26 steps and 197k tokens, against 9 steps and 33k tokens. The report shows this trade-off.

---

## Part 12: Hands-on exercises

Open the terminal in VS Code (**View → Terminal**) inside the project folder. All these commands were checked on your Mac.

### Exercise 1: run the unit tests
```bash
uv run pytest -v
```
You'll see all 88 tests with `PASSED`. Read their names: each name says what it checks, for example `test_a_passing_command_is_not_a_reproduction`.

### Exercise 2: break something on purpose, and watch a test catch it
1. Open [repo_bug_hunter/tools.py](repo_bug_hunter/tools.py) and find `REPRO_ATTEMPTS = 3`.
2. Change it to `5` and save.
3. Run `uv run pytest tests/test_tools.py -v`.
4. You'll see `test_edits_unlock_after_repeated_failed_attempts` **FAIL**. The test expects the files to unlock after 3 tries.
5. **Change it back to `3`**, then run the tests again. They all pass.

*Lesson:* this is why tests exist. They catch changes that break the rules.

### Exercise 3: read a real trajectory
```bash
python3 -c "
import json
t = json.load(open('runs/local_pilot_tf/trajs/psf__requests-1142.json'))
for s in t['steps']:
    for a in s['actions']:
        print(s['i'], a['tool'], '->', a['output'][:100].replace(chr(10), ' '))
"
```
You'll see every step of the test-first run. Find step 7 (blocked) and step 10 (recorded).

### Exercise 4: compare the two patches
```bash
cat logs/run_evaluation/local_pilot/local_pilot/psf__requests-1142/patch.diff
cat logs/run_evaluation/local_pilot_tf/local_pilot_tf/psf__requests-1142/patch.diff
```
Read the `-` and `+` lines. Which file does each one change?

### Exercise 5: produce the before/after report
```bash
uv run repo-bug-hunter analyze runs/local_pilot runs/local_pilot_tf
```
Find "McNemar exact p = 1.000" and the sentence that follows it. Now you know what it means.

### Exercise 6: open the replay website
```bash
uv run repo-bug-hunter viewer runs/local_pilot runs/local_pilot_tf --out /tmp/repo-bug-hunter-site
python3 -m http.server -d /tmp/repo-bug-hunter-site 8000
```
Open http://localhost:8000 in your browser. Click a run, then use the arrow keys to move through the steps. Press **Ctrl+C** in the terminal to stop the server.

### Exercise 7: play with the statistics
```bash
uv run python -c "
from repo_bug_hunter.analyze import wilson, mcnemar_exact
print('20 of 50  ->', wilson(20, 50))
print('40 of 100 ->', wilson(40, 100))
print('flips 3 vs 7  -> p =', mcnemar_exact(3, 7))
print('flips 2 vs 12 -> p =', mcnemar_exact(2, 12))
"
```
Try your own numbers. Notice that 40 of 100 gives a narrower range than 20 of 50, even though both are 40%.

### Exercise 8: watch the agent fix a toy bug (a few minutes, free)
Get a free key at https://openrouter.ai/settings/keys, make sure Docker Desktop is running, then:
```bash
export OPENROUTER_API_KEY=...        # your key; never commit it
uv run repo-bug-hunter doctor        # is everything ready?
uv run repo-bug-hunter smoke
uv run repo-bug-hunter smoke --variant test_first
```
Watch each step print live. The second command shows the test-first rule in action. The toy repository lives in a throwaway Docker container, like the real tasks. For the local model instead, keep Ollama running and add `--provider ollama --model gemma4-32k`.

---

## Part 13: Explaining it in an interview

### The 30-second pitch

> "I built a small coding agent that fixes real GitHub bugs, and I measured it on SWE-bench Verified, the standard benchmark of real Python bugs, where each fix is checked by the project's own hidden tests. The agent runs in the same Docker container as the official grader. It works with free OpenRouter models by default, or Claude, or a local model, and anyone can run the whole experiment on GitHub's free machines from a fork. I tested one idea: forcing the agent to write a failing test before it's allowed to edit code. The harness enforces this with a file lock. I compare the two versions on the same tasks with paired statistics, so I can say whether an improvement is real or just noise. Every failure is sorted into a category, and every run can be replayed step by step on a static website."

### Common questions and simple answers

**Q: Why not just ask the model to write a test in the prompt?**
A: A model can ignore a prompt. A lock can't be ignored. Enforcing it makes the test-first rule the *only* difference between the two versions, so I can measure its effect.

**Q: How do you know your results aren't just luck?**
A: I compare both versions on the same tasks, and I use McNemar's test, which only looks at tasks where the two versions disagree. I also give confidence intervals. If p is above 0.05, the report says the difference could be noise.

**Q: Where does your agent fail?**
A: Every unresolved task gets one category, such as wrong file, broke other tests or gave up. For example, in my first real run, the baseline edited `api.py` when the bug was in `models.py`, so it was classified as "wrong file".

**Q: Why run inside the grader's Docker image?**
A: Then "the tests passed for the agent" means the same as "the tests passed for the grader". I also switch the network off, so the agent can't install packages or look up the real fix.

**Q: Your scores are lower than the top agents. Why is this project useful?**
A: The goal isn't to beat them. It shows that I can build an agent, measure it honestly, find where it fails, and test an improvement with evidence. That's what teams need from an AI engineer.

**Q: What bugs did you find in your own system?**
A: Examples: stale `.pyc` files that made Python run old code after an edit; junk files like `.pyc` and plots leaking into patches; and commands that couldn't even run being counted as valid reproductions. Each would have made the results wrong, and each now has a test.

**Q: Why build this when GitHub Copilot can already fix bugs?**
A: Copilot is a product you use; its prompts and tools are closed, and it can't tell you whether a change to an agent helps. repo-bug-hunter answers that question: every fix is graded by hidden tests, two versions are compared on the same tasks with paired statistics, and everything is visible and changeable. Building and measuring an agent is the skill I wanted to show, not using one.

**Q: SWE-bench Verified is said to be contaminated. Why use it?**
A: In 2026 OpenAI stopped reporting it because frontier models had memorized some fixes and some tests were flawed, so absolute scores can be inflated. My main result is a paired comparison with the same model, and memorization affects both variants equally, so the difference is much less affected. I also built `repo-bug-hunter pr`, which turns any recent merged pull request into a task graded by its own tests, so I can test on bugs fixed after the model was trained.

**Q: How does it run without a local model or Docker?**
A: The default model is a free OpenRouter model, reached through the same OpenAI-compatible adapter as local models. The runs happen on GitHub Actions: one job per task on a fresh machine with Docker, then a job that merges the results and publishes the replay site. Free models have daily limits, so a run stops cleanly and resumes the next day.

**Q: What would you do next?**
A: Run 50 tasks per variant, repeat each run to check stability, and look through the failure categories in the replays to find the next idea to test.

---

## Part 14: What to learn next

Learn in this order. Each step makes the next one easier.

1. **Python basics**: the official tutorial at https://docs.python.org/3/tutorial/
2. **Git**: the free book at https://git-scm.com/book. Read chapters 1–3.
3. **Docker**: https://docs.docker.com/get-started/
4. **Tool calling**: read the "tool use" section of Anthropic's documentation, then read [repo_bug_hunter/agent.py](repo_bug_hunter/agent.py) again. It will make much more sense.
5. **Agents**: mini-swe-agent, a very small coding agent, at https://github.com/SWE-agent/mini-swe-agent. Compare it with ours.
6. **SWE-bench**: https://www.swebench.com. Look at the leaderboard and browse a few tasks.
7. **Statistics**: search for "confidence intervals" and "hypothesis testing" on Khan Academy. Then come back to Part 9.

**Best way to learn this project:** read the files in this order. Each one is short.
[tasks.py](repo_bug_hunter/tasks.py) → [env.py](repo_bug_hunter/env.py) → [tools.py](repo_bug_hunter/tools.py) → [agent.py](repo_bug_hunter/agent.py) → [run.py](repo_bug_hunter/run.py) → [evaluate.py](repo_bug_hunter/evaluate.py) → [analyze.py](repo_bug_hunter/analyze.py)

After this guide, read [IMPLEMENTATION.md](IMPLEMENTATION.md). You'll now understand every word in it.
