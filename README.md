# 👻 PossessedShell

*A Victorian ghost is trapped in your terminal. It mourns every command you kill, and it tells you how to fix them.*

```
            .-~~~~~~~~~-.
          .'   _     _   '.            ___________
         /    (@)   (@)    \          |           |
        |         ^         |         |  R.I.P.   |
        |     .-------.     |         |           |
         \    \ ~~~~~ /    /          | here lies |
          |    '-----'    |           |  your     |
          |               |           |  process  |
          | /\   /\   /\  |         __|___________|__
          |/  \_/  \_/  \_|        /////////////////
☠  THE PROCESS `python3 -c 'import ectoplasm'` HAS DIED  (exit 1)  ☠

  Alas! python3 hath perished, its last breath a ModuleNotFoundError for
  'ectoplasm'. I drew the curtains and stopped every clock in the house.

  ⚰  Diagnosis: The Python package 'ectoplasm' is not installed in this environment.

╭─ the ghost's remedy ───────────────╮
│  python3 -m pip install ectoplasm  │
╰────────────────────────────────────╯
  The remedy waits on your next prompt. Enter to accept, Ctrl-U to refuse.
  whispered by gemma2:2b
```

PossessedShell is an ambient CLI companion for zsh and bash, built for Hacktoberfest week 4. It does three things:

- **The Failing Command Curse.** When a command exits non-zero, it captures the command, the stderr and the exit code. It traces them to **Sentry**, asks **Gemma 2** (running locally in **Ollama**) for a diagnosis, and shows a ghostly interruption. The exact fix command is then pre-typed on your next prompt in zsh, or placed in history in bash.
- **The Forbidden Ritual Interceptor.** Destructive commands are stopped *before* they run, and the ghost asks whether you truly wish to summon such destruction. Covered: `rm -rf`, `git push --force`, `git reset --hard`, `chmod 777`, `DROP DATABASE`, `mkfs`, `dd of=/dev/…`, `curl | sh`, fork bombs and more.
- **The Ectoplasm Leak Detector.** A background check on CPU and RAM. It raises a spooky warning when the machine runs hot for 15 seconds or one process is devouring memory.

It also has an optional ghost memory on **Backboard**. With an API key set, the ghost remembers your past sins across sessions: *"Again, mortal? `python3` has died before by your hand…"*

## How it works

```
 your shell (zsh / bash)                         possessed-shell daemon  (~/.possessed/daemon.sock)
 ───────────────────────                         ──────────────────────────────────────────────────
 preexec   remember command, truncate stderr log   ┌─► Sentry: ShellCommandFailed event + stderr.txt,
 exec 2> >(tee …)   copy stderr to session log     │    transaction  shell.curse  ─┬ ghost.snapshot
 precmd    exit != 0 ─► possessed-shell curse ─────┤                               ├ ghost.memory.recall ─► Backboard (opt-in)
                        (spinner, then typewriter) │                               └ ghost.llm ─► Ollama gemma2:2b (localhost)
           pre-fill fix ◄──── haunting JSON ◄──────┘    fallback: offline grimoire of heuristics if Gemma is silent
 accept-line / DEBUG trap ─► possessed-shell ritual ─► y/N on /dev/tty, exit 66 = banished ─► Sentry warning
 precmd    omen.json present? ─► possessed-shell omen ◄── ectoplasm monitor (psutil, every 5s)
```

- **Hooks.** These are plain shell: zsh `preexec`/`precmd` plus an `accept-line` widget, and bash's `DEBUG` trap plus `PROMPT_COMMAND`. Python starts only when a command fails, when a command line matches the destructive-command prefilter regex, or when an omen is waiting. Successful everyday commands cost nothing.
- **Daemon.** It keeps Gemma warm (`keep_alive: 30m`), initialises Sentry, and runs the resource monitor. It talks newline-delimited JSON over a `0600` Unix socket. If it is not running, the hook command holds the séance in-process and wakes the daemon for next time.
- **Gemma output.** Gemma is asked for strict JSON (`{lament, diagnosis, fix}`) via Ollama's `format` JSON schema, so the fix can be extracted reliably. The ghost never runs a fix. If its own remedy is itself a forbidden ritual, it says so.
- **Offline fallback.** If Ollama is down, the ghost reads from an offline grimoire. It handles command-not-found typos, missing Python modules, a missing git upstream, misspelt paths, ports in use, OOM kills and more, so the demo never falls flat.

> **A note on Backboard.** [Backboard](https://backboard.io) is a cloud API for AI memory and assistants (`pip install backboard-sdk`). It can't hook a terminal or draw ASCII art. PossessedShell uses plain shell hooks for monitoring and [`rich`](https://github.com/Textualize/rich) for rendering. Backboard is used for what it is good at: **persistent memory of your past failures**. It is opt-in and off by default, so the default install stays 100% offline.

## Project layout

```
PossessedShell/
├── README.md
├── requirements.txt           # pip install -r requirements.txt
├── pyproject.toml             # console script: possessed-shell
├── possessed_shell/
│   ├── cli.py                 # init | curse | ritual | omen | daemon | doctor | sins | summon
│   ├── daemon.py              # asyncio Unix-socket daemon: Sentry, Gemma warm-up, ectoplasm monitor
│   ├── seance.py              # the curse pipeline (Sentry → Backboard recall → Gemma → fallback)
│   ├── brain.py               # Ollama /api/chat (or OpenAI-compatible /v1) call to Gemma 2
│   ├── prompts.py             # 👻 the ghost's system prompt, curse template, JSON schema
│   ├── banners.py             # 👻 ASCII apparitions: wailing ghost, ritual skull, ectoplasm blob
│   ├── render.py              # rich: blood red / ghostly cyan palette, glitch reveal, typewriter, spinner
│   ├── telemetry.py           # Sentry init, transactions/spans, ShellCommandFailed events, scrubbing
│   ├── memory.py              # Backboard ghost memory (opt-in)
│   ├── rituals.py             # destructive-command rules + the shell-side prefilter regex
│   ├── remedies.py            # offline grimoire of fixes when Gemma is silent
│   ├── ectoplasm.py           # psutil snapshots + sustained-spike detection
│   ├── redact.py              # secret scrubbing before anything leaves the machine
│   ├── client.py  models.py  config.py
│   └── hooks/possessed.zsh  hooks/possessed.bash
└── tests/                     # unit tests + real zsh/bash sessions driven through a pty
```

## Setup guide

**1. Summon the brain: Ollama with Gemma 2**

```bash
# macOS: brew install ollama        Linux:
curl -fsSL https://ollama.com/install.sh | sh
ollama serve &                 # skip if Ollama already runs as a service / the desktop app
ollama pull gemma2:2b          # or gemma2:9b for a wiser (slower) ghost: export POSSESSED_MODEL=gemma2:9b
```

**2. Install PossessedShell**

```bash
git clone https://github.com/realarbird/PossessedShell && cd PossessedShell
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt && pip install -e .
# or, without a venv to manage:  pipx install .
```

**3. (Optional) Telemetry and memory.** Put these in your shell rc file, *above* the hook line.

```bash
export SENTRY_DSN="https://<key>@o<org>.ingest.sentry.io/<project>"   # trace failures + the daemon
export BACKBOARD_API_KEY="<your key>"                                 # let the ghost remember your sins
```

**4. Possess your shell**

```bash
# zsh
echo 'eval "$(possessed-shell init zsh)"' >> ~/.zshrc
# bash (4.0+): must be the LAST line of ~/.bashrc
echo 'eval "$(possessed-shell init bash)"' >> ~/.bashrc

exec $SHELL                  # reload; the daemon wakes up in the background
possessed-shell doctor       # checks Gemma, the daemon, the hook, Sentry and Backboard
```

`init` bakes the absolute path of the `possessed-shell` you ran it with into the hook, so it keeps working outside the venv.

**5. Test a haunted crash**

```bash
ls /crypt/of/nothing                 # the curse: stderr captured, ghost mourns, fix pre-typed
python3 -c "import ectoplasm"        # Gemma diagnoses the missing module
gti status                           # a typo, and the ghost knows what you meant
mkdir graveyard && rm -rf graveyard  # the Forbidden Ritual Interceptor: answer N to banish it
git push --force                     # (in a repo) another ritual
possessed-shell omen --demo          # preview an Ectoplasm Leak warning
possessed-shell summon -- python3 -c "1/0"   # haunt any doomed command without the hook
possessed-shell sins                 # what the ghost remembers (Backboard)
```

## The ghost's words

The full system prompt is in [`possessed_shell/prompts.py`](possessed_shell/prompts.py). In short, Gemma plays *Cornelius Vale, a Victorian telegraph engineer who perished in 1887, bound inside this machine*. It must answer with JSON only:

| key | content |
|---|---|
| `lament` | 2–3 sentences (≤ 60 words) of Victorian mourning that name the actual command and error. No advice. |
| `diagnosis` | one plain modern-English sentence naming the root cause |
| `fix` | exactly **one** runnable shell command for your shell and OS, or `""` |

The prompt forbids inventing files, flags or packages the error doesn't imply, and it bans destructive remedies unless there is no other way. The curse template also gives Gemma:
- the exit code, cwd, shell and OS
- the stderr tail
- a system snapshot (CPU, RAM, disk, the hungriest process)
- any past sins Backboard remembers

The three banners in [`banners.py`](possessed_shell/banners.py) (wailing ghost, ritual skull with candles, ectoplasm blob) flicker in through glitch static before they settle. Text is spelled out typewriter-style in ghostly cyan, and warnings come in blood red.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `POSSESSED_MODEL` | `gemma2:2b` | model name |
| `OLLAMA_HOST` / `POSSESSED_LLM_URL` | `http://127.0.0.1:11434` | where the brain lives |
| `POSSESSED_LLM_API` | `ollama` | `openai` for any OpenAI-compatible local server (e.g. an OpenClaw gateway or llama.cpp at `/v1/chat/completions`) |
| `POSSESSED_LLM_TIMEOUT` | `60` | seconds before the ghost gives up on Gemma and reads the grimoire |
| `SENTRY_DSN`, `POSSESSED_ENV` | unset, `haunted` | Sentry tracing (off without a DSN) |
| `BACKBOARD_API_KEY` | unset | Backboard ghost memory (off without a key) |
| `POSSESSED_DISABLED` | `0` | `1` silences the ghost in this shell |
| `POSSESSED_RITUALS` / `POSSESSED_OMENS` | `1` | turn the interceptor / resource warnings off with `0` |
| `POSSESSED_CAPTURE_STDERR` | `1` | `0` disables stderr capture (see trade-offs) |
| `POSSESSED_NO_ANIMATION` | `0` | `1` prints instantly, no glitch or typewriter |
| `POSSESSED_TYPEWRITER_CPS` | `90` | typewriter speed |
| `POSSESSED_CPU_THRESHOLD` / `_MEM_THRESHOLD` / `_PROC_MEM_THRESHOLD` | `90` / `90` / `30` | omen thresholds in % (sustained for 3 readings, 10-minute cooldown) |
| `POSSESSED_AUTOSTART` | `1` | `0` stops hooks from starting the daemon |
| `POSSESSED_HOME` | `~/.possessed` | socket, pid, logs, per-session state |

Daemon controls are `possessed-shell daemon start|stop|restart|status|run`. Use `run` to stay in the foreground and watch the logs.

## Privacy and trade-offs

- **The brain is local.** Commands and stderr go to Gemma on `localhost` only.
- **Sentry and Backboard are opt-in.** Before anything goes to either one, `redact.py` scrubs it. It removes passwords, tokens, API keys, AWS and GitHub keys, JWTs, private keys and credentials in URLs, through `before_send`/`before_send_transaction` for Sentry and before each memory write for Backboard. Backboard only ever receives a one-line summary per failure. Point `SENTRY_DSN` at a self-hosted Sentry to keep telemetry on your own network.
- **stderr capture** works by sending the shell's stderr through `tee` into `~/.possessed/sessions/<pid>/stderr.log`, which is mode `0700` and truncated before each command. Because stderr is then a pipe and not a TTY, some tools drop colours or progress bars on stderr (`git` progress, for example). Set `POSSESSED_CAPTURE_STDERR=0` if that bothers you. The ghost still works, with less to go on.
- **bash** uses `shopt -s extdebug`, so a `DEBUG` trap can veto a command. This also turns on function tracing, so the hook ignores anything that isn't a top-level command you typed. It chains any existing `DEBUG`/`EXIT` trap, but it must be sourced after other prompt tools (starship, etc.).
- **zsh** keeps a banished command on the line, unrun, so you can fix it.
- **The interceptor fails open.** If PossessedShell itself breaks, your command runs. Only an explicit "no" (exit code 66) blocks it.

## Development

```bash
pip install -e ".[dev,backboard]"
pytest            # unit tests + end-to-end zsh/bash sessions in a pty (skipped if a shell is missing)
```

The end-to-end tests start real interactive shells with the hook loaded. They check that:
- a failing command is cursed using its captured stderr;
- the fix lands in history in bash, or pre-typed on the prompt in zsh;
- `grep` with no match is left alone;
- a banished `rm -rf` line runs nothing at all, while a summoned one runs;
- omens appear at the next prompt.

They run with Gemma unreachable, so the offline grimoire answers.
