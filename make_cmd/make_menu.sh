#!/bin/bash

set -e

declare -a targets=()
declare -a descriptions=()

for MAKEFILE in "${@:-Makefile}"; do
    while IFS= read -r line; do
        if [[ "$line" =~ ^([a-zA-Z0-9_-]+):.*\#\#\ (.+)$ ]]; then
            targets+=("${BASH_REMATCH[1]}")
            descriptions+=("${BASH_REMATCH[2]}")
        fi
    done < "$MAKEFILE"
done

count=${#targets[@]}

if [[ $count -eq 0 ]]; then
    echo "No annotated targets found in $MAKEFILE"
    echo "Annotate targets with:  target: ## Description"
    exit 1
fi

max_name=0
for t in "${targets[@]}"; do
    [[ ${#t} -gt $max_name ]] && max_name=${#t}
done

BOLD='\033[1m'
GREEN='\033[32m'
CYAN='\033[36m'
DIM='\033[2m'
BG_GREEN='\033[42m'
WHITE='\033[97m'
NC='\033[0m'

selected=0
rendered=0

cleanup() {
    tput cnorm 2>/dev/null
    stty sane 2>/dev/null
}
trap cleanup EXIT

tput civis 2>/dev/null

draw() {
    if ((rendered)); then
        for ((i = 0; i < total_lines; i++)); do
            echo -en "\033[A\033[2K"
        done
    fi
    rendered=1
    total_lines=0

    echo "";                                                           ((++total_lines))
    echo -e "  ${BOLD}${GREEN}Available commands:${NC}";               ((++total_lines))
    echo "";                                                           ((++total_lines))

    for i in "${!targets[@]}"; do
        local num=$((i + 1))
        if [[ $i -eq $selected ]]; then
            printf "  ${BG_GREEN}${WHITE}${BOLD} %d) %-${max_name}s  →  %s ${NC}\n" \
                "$num" "${targets[$i]}" "${descriptions[$i]}"
        else
            printf "  ${CYAN} %d) %-${max_name}s${DIM}  →  %s${NC}\n" \
                "$num" "${targets[$i]}" "${descriptions[$i]}"
        fi
        ((++total_lines))
    done

    echo "";                                                                                    ((++total_lines))
    echo -e "  ${DIM}↑/↓ Navigate  •  Enter Select  •  Type number or name  •  q Quit${NC}";   ((++total_lines))
}

run_target() {
    echo ""
    echo -e "  ${GREEN}▶ make ${targets[$selected]}${NC}"
    echo ""
    exec make "${targets[$selected]}"
}

draw

while true; do
    IFS= read -rsn1 key < /dev/tty

    case "$key" in
        $'\x1b')
            read -rsn2 -t 0.1 rest < /dev/tty
            case "$rest" in
                '[A') ((selected > 0)) && ((selected--)) || true; draw ;;
                '[B') ((selected < count - 1)) && ((selected++)) || true; draw ;;
            esac
            ;;
        '')
            run_target
            ;;
        q|Q)
            echo ""
            echo "  Cancelled."
            exit 0
            ;;
        [0-9])
            idx=$((key - 1))
            if ((idx >= 0 && idx < count)); then
                selected=$idx
                draw
            fi
            ;;
        *)
            buf="$key"
            enter_pressed=0
            while IFS= read -rsn1 -t 0.4 ch < /dev/tty; do
                if [[ -z "$ch" ]]; then
                    enter_pressed=1
                    break
                fi
                buf+="$ch"
            done

            for i in "${!targets[@]}"; do
                if [[ "${targets[$i]}" == "${buf}"* ]]; then
                    selected=$i
                    break
                fi
            done
            draw

            if ((enter_pressed)); then
                run_target
            fi
            ;;
    esac
done
