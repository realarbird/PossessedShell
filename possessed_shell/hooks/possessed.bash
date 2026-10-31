# PossessedShell hook for bash (3.2+, including macOS /bin/bash).  Load it as the LAST line of ~/.bashrc with:
#     eval "$(possessed-shell init bash)"
#
# - DEBUG trap (preexec): remember each command; destructive ones are intercepted before they run.
# - PROMPT_COMMAND (precmd): when the command died (exit != 0), summon the ghost.
# - stderr capture: fd 2 is copied into a per-session log with tee, so the ghost can read the error.
# - omens: CPU/RAM warnings dropped by the daemon are shown at the next prompt.

[[ $- == *i* ]] || return 0
[[ -n ${__POSSESSED_LOADED:-} ]] && return 0
if (( BASH_VERSINFO[0] < 3 || (BASH_VERSINFO[0] == 3 && BASH_VERSINFO[1] < 2) )); then
  echo "possessed-shell: bash ${BASH_VERSION} is too old to be haunted (need 3.2+); try zsh." >&2
  return 0
fi
__POSSESSED_LOADED=1

__POSSESSED_BIN=(__POSSESSED_BIN__)
__POSSESSED_RITUAL_RE='__POSSESSED_RITUAL_RE__'
__POSSESSED_DIR="${POSSESSED_HOME:-$HOME/.possessed}/sessions/$$"
__POSSESSED_ERR="$__POSSESSED_DIR/stderr.log"
__POSSESSED_CMD=""
__POSSESSED_START=""
__POSSESSED_START_SEC=0
__POSSESSED_AT_PROMPT=0
__POSSESSED_BLOCKED=0
__POSSESSED_HISTNUM=""
export POSSESSED_HOOKED=bash

mkdir -p -m 700 -- "$__POSSESSED_DIR" 2>/dev/null

if [[ ${POSSESSED_CAPTURE_STDERR:-1} != 0 ]]; then
  : >| "$__POSSESSED_ERR"
  exec 2> >(command tee -a -- "$__POSSESSED_ERR" >&2)
fi

__possessed_awake() {
  [[ -z ${POSSESSED_DISABLED:-} || ${POSSESSED_DISABLED} == 0 ]]
}

# Print the command of an existing trap, so ours can be chained after it.
__possessed_prev_trap() {
  local prev
  prev=$(trap -p "$1")
  prev=${prev#trap -- \'}
  prev=${prev%\' *}
  prev=${prev//\'\\\'\'/\'}
  while [[ $prev == *[\;[:space:]] ]]; do prev=${prev%?}; done
  printf '%s' "$prev"
}

# The DEBUG trap. extdebug also turns on function tracing, so this fires inside functions and
# command substitutions (PS1's included) too: anything that is not ours, at top level, is ignored.
__possessed_preexec() {
  (( BASH_SUBSHELL == 0 )) && [[ ${BASHPID:-$$} == "$$" ]] || return 0
  case $BASH_COMMAND in __possessed_*) return 0 ;; esac
  case ${FUNCNAME[1]:-} in __possessed_*) return 0 ;; esac
  if [[ $__POSSESSED_AT_PROMPT != 1 ]]; then
    # Banished line: skip every remaining simple command on it (extdebug skips on non-zero).
    [[ $__POSSESSED_BLOCKED == 1 ]] && return 1
    return 0
  fi
  [[ -n ${COMP_LINE:-} ]] && return 0
  __POSSESSED_AT_PROMPT=0

  local cmd=$BASH_COMMAND line
  line=$(HISTTIMEFORMAT= builtin history 1)
  if [[ $line =~ ^[[:space:]]*([0-9]+)[*]?[[:space:]]+(.*)$ ]] && [[ ${BASH_REMATCH[1]} != "$__POSSESSED_HISTNUM" ]]; then
    __POSSESSED_HISTNUM=${BASH_REMATCH[1]}
    cmd=${BASH_REMATCH[2]}
  fi
  __POSSESSED_CMD=$cmd
  __POSSESSED_START=${EPOCHREALTIME:-}
  __POSSESSED_START_SEC=$SECONDS
  [[ -f $__POSSESSED_ERR ]] && : >| "$__POSSESSED_ERR"

  if [[ ${POSSESSED_RITUALS:-1} != 0 ]] && __possessed_awake; then
    local _nocase=0 _matched=0
    shopt -q nocasematch && _nocase=1
    shopt -s nocasematch
    [[ $cmd =~ $__POSSESSED_RITUAL_RE ]] && _matched=1
    (( _nocase == 0 )) && shopt -u nocasematch
    if (( _matched )); then
      "${__POSSESSED_BIN[@]}" ritual --session $$ --shell bash -- "$cmd"
      if [[ $? == 66 ]]; then
        __POSSESSED_BLOCKED=1
        return 1
      fi
    fi
  fi
  return 0
}

__possessed_precmd() {
  local ec=$?
  local cmd=$__POSSESSED_CMD blocked=$__POSSESSED_BLOCKED line
  __POSSESSED_CMD=""
  __POSSESSED_BLOCKED=0
  if [[ -z $cmd && $blocked != 1 ]]; then
    # In bash 4+, pipeline elements run in subshells so preexec could not set __POSSESSED_CMD in the parent.
    line=$(HISTTIMEFORMAT= builtin history 1)
    if [[ $line =~ ^[[:space:]]*([0-9]+)[*]?[[:space:]]+(.*)$ ]] && [[ ${BASH_REMATCH[1]} != "$__POSSESSED_HISTNUM" ]]; then
      __POSSESSED_HISTNUM=${BASH_REMATCH[1]}
      cmd=${BASH_REMATCH[2]}
    fi
  fi
  if __possessed_awake && [[ -n $cmd && $blocked != 1 ]] && (( ec != 0 && ec != 130 && ec != 141 && ec != 148 )); then
    local -a timing=()
    if [[ -n $__POSSESSED_START ]]; then
      timing=(--started "$__POSSESSED_START")
    else
      timing=(--duration "$(( SECONDS - __POSSESSED_START_SEC ))")
    fi
    "${__POSSESSED_BIN[@]}" curse --session $$ --shell bash --exit-code "$ec" \
      "${timing[@]}" \
      --stderr-file "$__POSSESSED_ERR" --cwd "$PWD" -- "$cmd"
    [[ -f $__POSSESSED_ERR ]] && : >| "$__POSSESSED_ERR"
    if [[ -s $__POSSESSED_DIR/fix ]]; then
      # The ghost's remedy is placed in history: press Up to summon it. It never runs by itself.
      builtin history -s "$(<"$__POSSESSED_DIR/fix")"
      command rm -f -- "$__POSSESSED_DIR/fix"
    fi
  fi
  if __possessed_awake && [[ -s $__POSSESSED_DIR/omen.json ]]; then
    "${__POSSESSED_BIN[@]}" omen --session $$
  fi
  return $ec
}

# Runs at the very end of PROMPT_COMMAND: the next DEBUG trap belongs to a command the user typed.
__possessed_arm() {
  local line
  line=$(HISTTIMEFORMAT= builtin history 1)
  if [[ $line =~ ^[[:space:]]*([0-9]+)[*]?[[:space:]]+ ]]; then
    __POSSESSED_HISTNUM=${BASH_REMATCH[1]}
  fi
  __POSSESSED_AT_PROMPT=1
}

__possessed_exit() {
  command rm -f -- "$__POSSESSED_DIR/stderr.log" "$__POSSESSED_DIR/fix" "$__POSSESSED_DIR/omen.json" 2>/dev/null
  command rmdir -- "$__POSSESSED_DIR" 2>/dev/null
}

if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
  PROMPT_COMMAND=(__possessed_precmd "${PROMPT_COMMAND[@]}" __possessed_arm)
else
  __possessed_pc=${PROMPT_COMMAND:-}
  while [[ $__possessed_pc == *[\;[:space:]] ]]; do __possessed_pc=${__possessed_pc%?}; done
  PROMPT_COMMAND="__possessed_precmd${__possessed_pc:+; $__possessed_pc}; __possessed_arm"
  unset __possessed_pc
fi

[[ ${POSSESSED_RITUALS:-1} != 0 ]] && shopt -s extdebug
__possessed_prev=$(__possessed_prev_trap DEBUG)
trap "${__possessed_prev:+$__possessed_prev; }__possessed_preexec" DEBUG
__possessed_prev=$(__possessed_prev_trap EXIT)
trap "${__possessed_prev:+$__possessed_prev; }__possessed_exit" EXIT
unset __possessed_prev

# Wake the daemon (Gemma warm-up, Sentry, ectoplasm monitor) without slowing the shell down.
if [[ ${POSSESSED_AUTOSTART:-1} != 0 ]]; then
  ( "${__POSSESSED_BIN[@]}" daemon start --quiet >/dev/null 2>&1 & )
fi
