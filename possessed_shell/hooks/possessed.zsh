# PossessedShell hook for zsh.  Load it from ~/.zshrc with:
#     eval "$(possessed-shell init zsh)"
#
# - preexec/precmd: remember each command, and when it dies (exit != 0) summon the ghost.
# - stderr capture: fd 2 is copied into a per-session log with tee, so the ghost can read the error.
# - accept-line widget: destructive commands are intercepted before they run.
# - omens: CPU/RAM warnings dropped by the daemon are shown at the next prompt.

[[ -o interactive ]] || return 0
(( ${+__POSSESSED_LOADED} )) && return 0
typeset -g __POSSESSED_LOADED=1

zmodload zsh/datetime 2>/dev/null
autoload -Uz add-zsh-hook

typeset -ga __POSSESSED_BIN
__POSSESSED_BIN=(__POSSESSED_BIN__)
typeset -g __POSSESSED_RITUAL_RE='__POSSESSED_RITUAL_RE__'
typeset -g __POSSESSED_DIR="${POSSESSED_HOME:-$HOME/.possessed}/sessions/$$"
typeset -g __POSSESSED_ERR="$__POSSESSED_DIR/stderr.log"
typeset -g __POSSESSED_CMD=""
typeset -g __POSSESSED_START=""
export POSSESSED_HOOKED=zsh

mkdir -p -m 700 -- "$__POSSESSED_DIR" 2>/dev/null

if [[ ${POSSESSED_CAPTURE_STDERR:-1} != 0 ]]; then
  : >| "$__POSSESSED_ERR"
  # zsh draws its prompt on the terminal itself, so only real stderr flows through tee.
  exec 2> >(command tee -a -- "$__POSSESSED_ERR" >&2)
fi

__possessed_awake() {
  [[ -z ${POSSESSED_DISABLED:-} || ${POSSESSED_DISABLED} == 0 ]]
}

__possessed_preexec() {
  __POSSESSED_CMD=$1
  __POSSESSED_START=${EPOCHREALTIME:-}
  [[ -f $__POSSESSED_ERR ]] && : >| "$__POSSESSED_ERR"
}

__possessed_precmd() {
  local ec=$?
  local cmd=$__POSSESSED_CMD
  __POSSESSED_CMD=""
  if __possessed_awake && [[ -n $cmd ]] && (( ec != 0 && ec != 130 && ec != 141 && ec != 148 )); then
    "${__POSSESSED_BIN[@]}" curse --session $$ --shell zsh --exit-code $ec \
      ${__POSSESSED_START:+--started} ${__POSSESSED_START:+$__POSSESSED_START} \
      --stderr-file "$__POSSESSED_ERR" --cwd "$PWD" -- "$cmd"
    if [[ -s $__POSSESSED_DIR/fix ]]; then
      # The ghost's remedy waits, pre-typed, on the next prompt. It never runs by itself.
      print -rz -- "$(<$__POSSESSED_DIR/fix)"
      command rm -f -- "$__POSSESSED_DIR/fix"
    fi
  fi
  if __possessed_awake && [[ -s $__POSSESSED_DIR/omen.json ]]; then
    "${__POSSESSED_BIN[@]}" omen --session $$
  fi
  return $ec
}

__possessed_zshexit() {
  command rm -f -- "$__POSSESSED_DIR/stderr.log" "$__POSSESSED_DIR/fix" "$__POSSESSED_DIR/omen.json" 2>/dev/null
  command rmdir -- "$__POSSESSED_DIR" 2>/dev/null
  return 0
}

add-zsh-hook preexec __possessed_preexec
# precmd must run first so it sees the dead command's real $?.
precmd_functions=(__possessed_precmd ${precmd_functions:#__possessed_precmd})
add-zsh-hook zshexit __possessed_zshexit

# The Forbidden Ritual Interceptor: wrap accept-line (Enter) so destructive commands need consent.
if [[ ${POSSESSED_RITUALS:-1} != 0 ]]; then
  if [[ ${widgets[accept-line]:-} == user:* ]]; then
    zle -A accept-line __possessed_orig_accept_line
  fi

  __possessed_accept_line() {
    emulate -L zsh
    if __possessed_awake && [[ ${(L)BUFFER} =~ $__POSSESSED_RITUAL_RE ]]; then
      zle -I
      "${__POSSESSED_BIN[@]}" ritual --session $$ --shell zsh -- "$BUFFER"
      if (( $? == 66 )); then
        # Banished: the command stays on the line, unrun, for you to reconsider.
        zle reset-prompt
        return 0
      fi
    fi
    if (( ${+widgets[__possessed_orig_accept_line]} )); then
      zle __possessed_orig_accept_line -- "$@"
    else
      zle .accept-line -- "$@"
    fi
  }
  zle -N accept-line __possessed_accept_line
fi

# Wake the daemon (Gemma warm-up, Sentry, ectoplasm monitor) without slowing the shell down.
if [[ ${POSSESSED_AUTOSTART:-1} != 0 ]]; then
  ( "${__POSSESSED_BIN[@]}" daemon start --quiet >/dev/null 2>&1 & )
fi
