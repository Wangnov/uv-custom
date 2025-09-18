#!/bin/sh
#
# UV + Conda/Mamba Environment Hook Injector for macOS & Linux
#
# This script intelligently detects installed shells (Bash, Zsh, Fish, Elvish)
# and injects a hook to sync UV_PROJECT_ENVIRONMENT with the active Conda/Mamba
# environment. It is idempotent and safe to run multiple times.
#
# Usage: ./setup_hooks.sh [--ignore_base]
#   --ignore_base: Exclude conda's default 'base' environment from UV sync
#

# --- Global Variables ---
IGNORE_BASE=false

# --- Helper Functions ---
show_help() {
    cat << EOF
Usage: $0 [OPTIONS]

This script sets up hooks to sync UV_PROJECT_ENVIRONMENT with active Conda/Mamba environments.

OPTIONS:
    --ignore_base    Exclude conda's default 'base' environment from UV sync
    -h, --help       Show this help message and exit

EXAMPLES:
    $0                Setup hooks with default behavior (includes base environment)
    $0 --ignore_base  Setup hooks excluding base environment from sync

EOF
}

parse_arguments() {
    while [ $# -gt 0 ]; do
        case $1 in
            --ignore_base)
                IGNORE_BASE=true
                shift
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                echo "Error: Unknown option '$1'"
                echo "Use --help for usage information."
                exit 1
                ;;
        esac
    done
}
say() {
    echo "› $1"
}

inject_code() {
    local config_file="$1"
    local marker="$2"
    local code_block="$3"
    local shell_name="$4"

    # Ensure the parent directory exists before writing.
    mkdir -p "$(dirname "$config_file")"
    # Ensure the config file itself exists.
    touch "$config_file"

    # Extract the shell type from marker to create end marker
    local shell_type=$(echo "$marker" | sed 's/.*-HOOK-\(.*\)-START/\1/')
    local end_marker="# UV-CONDA-HOOK-${shell_type}-END"

    # Check if hook already exists
    if grep -qF -- "$marker" "$config_file" 2>/dev/null; then
        say "Existing $shell_name hook found in $config_file. Replacing with updated version..."
        
        # Create a temporary file to store the cleaned content
        local temp_file=$(mktemp)
        
        # Remove existing hook block between START and END markers
        awk -v start="$marker" -v end="$end_marker" '
        BEGIN { in_block = 0 }
        $0 ~ start { in_block = 1; next }
        $0 ~ end { in_block = 0; next }
        !in_block { print }
        ' "$config_file" > "$temp_file"
        
        # Replace original file with cleaned content
        mv "$temp_file" "$config_file"
        
        say "Removed existing $shell_name hook from $config_file."
    fi
    
    # Always inject the new code block
    say "Injecting $shell_name hook into $config_file..."
    printf '\n%s\n' "$code_block" >> "$config_file"
    say "Successfully injected hook for $shell_name."
}

# --- Shell-Specific Setups ---
setup_bash() {
    if ! command -v bash >/dev/null 2>&1; then return; fi
    say "Bash detected. Setting up hook..."
    local config_file="$HOME/.bashrc"
    local marker="# UV-CONDA-HOOK-BASH-START"
    
    # Generate code block based on IGNORE_BASE setting
    local code_block
    if [ "$IGNORE_BASE" = "true" ]; then
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-BASH-START
_sync_mamba_uv_env() {
  if [ -n "$CONDA_PREFIX" ] && [ "$CONDA_DEFAULT_ENV" != "base" ]; then
    if [ -z "$UV_PROJECT_ENVIRONMENT" ] || [ "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX" ]; then
      export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
    fi
  else
    if [ -n "$UV_PROJECT_ENVIRONMENT" ]; then
      unset UV_PROJECT_ENVIRONMENT
    fi
  fi
}
if [[ ! "$PROMPT_COMMAND" =~ _sync_mamba_uv_env ]]; then
  PROMPT_COMMAND="_sync_mamba_uv_env;${PROMPT_COMMAND}"
fi
# UV-CONDA-HOOK-BASH-END
EOF
)
    else
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-BASH-START
_sync_mamba_uv_env() {
  if [ -n "$CONDA_PREFIX" ]; then
    if [ -z "$UV_PROJECT_ENVIRONMENT" ] || [ "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX" ]; then
      export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
    fi
  else
    if [ -n "$UV_PROJECT_ENVIRONMENT" ]; then
      unset UV_PROJECT_ENVIRONMENT
    fi
  fi
}
if [[ ! "$PROMPT_COMMAND" =~ _sync_mamba_uv_env ]]; then
  PROMPT_COMMAND="_sync_mamba_uv_env;${PROMPT_COMMAND}"
fi
# UV-CONDA-HOOK-BASH-END
EOF
)
    fi
    
    inject_code "$config_file" "$marker" "$code_block" "Bash"
}

setup_zsh() {
    if ! command -v zsh >/dev/null 2>&1; then return; fi
    say "Zsh detected. Setting up hook..."
    local config_file="$HOME/.zshrc"
    local marker="# UV-CONDA-HOOK-ZSH-START"
    
    # Generate code block based on IGNORE_BASE setting
    local code_block
    if [ "$IGNORE_BASE" = "true" ]; then
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-ZSH-START
_sync_mamba_uv_env() {
  if [ -n "$CONDA_PREFIX" ] && [ "base" != "$CONDA_DEFAULT_ENV" ]; then
    if [ -z "$UV_PROJECT_ENVIRONMENT" ] || [ "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX" ]; then
      export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
    fi
  else
    if [ -n "$UV_PROJECT_ENVIRONMENT" ]; then
      unset UV_PROJECT_ENVIRONMENT
    fi
  fi
}
if [[ ! " ${precmd_functions[@]} " =~ " _sync_mamba_uv_env " ]]; then
  precmd_functions+=(_sync_mamba_uv_env)
fi
# UV-CONDA-HOOK-ZSH-END
EOF
)
    else
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-ZSH-START
_sync_mamba_uv_env() {
  if [ -n "$CONDA_PREFIX" ]; then
    if [ -z "$UV_PROJECT_ENVIRONMENT" ] || [ "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX" ]; then
      export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
    fi
  else
    if [ -n "$UV_PROJECT_ENVIRONMENT" ]; then
      unset UV_PROJECT_ENVIRONMENT
    fi
  fi
}
if [[ ! " ${precmd_functions[@]} " =~ " _sync_mamba_uv_env " ]]; then
  precmd_functions+=(_sync_mamba_uv_env)
fi
# UV-CONDA-HOOK-ZSH-END
EOF
)
    fi
    
    inject_code "$config_file" "$marker" "$code_block" "Zsh"
}

setup_fish() {
    if ! command -v fish >/dev/null 2>&1; then return; fi
    say "Fish detected. Setting up hook..."
    local config_file="$HOME/.config/fish/config.fish"
    local marker="# UV-CONDA-HOOK-FISH-START"
    
    # Generate code block based on IGNORE_BASE setting
    local code_block
    if [ "$IGNORE_BASE" = "true" ]; then
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-FISH-START
function _sync_mamba_uv_env --on-event fish_preexec
    if set -q CONDA_PREFIX; and test "$CONDA_DEFAULT_ENV" != "base"
        if not set -q UV_PROJECT_ENVIRONMENT; or test "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX"
            set -gx UV_PROJECT_ENVIRONMENT "$CONDA_PREFIX"
        end
    else
        if set -q UV_PROJECT_ENVIRONMENT
            set -e UV_PROJECT_ENVIRONMENT
        end
    end
end
# UV-CONDA-HOOK-FISH-END
EOF
)
    else
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-FISH-START
function _sync_mamba_uv_env --on-event fish_preexec
    if set -q CONDA_PREFIX
        if not set -q UV_PROJECT_ENVIRONMENT; or test "$UV_PROJECT_ENVIRONMENT" != "$CONDA_PREFIX"
            set -gx UV_PROJECT_ENVIRONMENT "$CONDA_PREFIX"
        end
    else
        if set -q UV_PROJECT_ENVIRONMENT
            set -e UV_PROJECT_ENVIRONMENT
        end
    end
end
# UV-CONDA-HOOK-FISH-END
EOF
)
    fi
    
    inject_code "$config_file" "$marker" "$code_block" "Fish"
}

setup_elvish() {
    if ! command -v elvish >/dev/null 2>&1; then return; fi
    say "Elvish detected. Setting up hook..."
    local config_file="$HOME/.config/elvish/rc.elv"
    local marker="# UV-CONDA-HOOK-ELVISH-START"
    
    # Generate code block based on IGNORE_BASE setting
    local code_block
    if [ "$IGNORE_BASE" = "true" ]; then
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-ELVISH-START
set edit:before-prompt = [ $@edit:before-prompt {
    if (and (has-env CONDA_PREFIX) (not (== $E:CONDA_DEFAULT_ENV “base”))) {
        if (not (has-env UV_PROJECT_ENVIRONMENT)) or (not (== $E:UV_PROJECT_ENVIRONMENT $E:CONDA_PREFIX)) {
            set-env UV_PROJECT_ENVIRONMENT $E:CONDA_PREFIX
        }
    } else {
        if (has-env UV_PROJECT_ENVIRONMENT) {
            unset-env UV_PROJECT_ENVIRONMENT
        }
    }
} ]
# UV-CONDA-HOOK-ELVISH-END
EOF
)
    else
        code_block=$(cat <<'EOF'
# UV-CONDA-HOOK-ELVISH-START
set edit:before-prompt = [ $@edit:before-prompt {
    if (has-env CONDA_PREFIX) {
        if (not (has-env UV_PROJECT_ENVIRONMENT)) or (not (== $E:UV_PROJECT_ENVIRONMENT $E:CONDA_PREFIX)) {
            set-env UV_PROJECT_ENVIRONMENT $E:CONDA_PREFIX
        }
    } else {
        if (has-env UV_PROJECT_ENVIRONMENT) {
            unset-env UV_PROJECT_ENVIRONMENT
        }
    }
} ]
# UV-CONDA-HOOK-ELVISH-END
EOF
)
    fi
    
    inject_code "$config_file" "$marker" "$code_block" "Elvish"
}

# --- Main Execution ---
# Parse command line arguments first
parse_arguments "$@"

echo "--- Setting up UV + Conda/Mamba Hooks for macOS/Linux ---"
if [ "$IGNORE_BASE" = "true" ]; then
    say "Running with --ignore_base: conda 'base' environment will be excluded from UV sync"
fi

setup_bash
setup_zsh
setup_fish
setup_elvish
echo "---------------------------------------------------------"
echo "Setup complete. Please restart your shell(s) to apply changes."