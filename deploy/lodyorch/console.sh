#!/bin/sh
# Console tmux de LODYORCH (observation seulement : le superviseur tourne dans lodyorch.service).
#   console.sh start   crée la session tmux « lodyorch » (fenêtres activite / status / journal / events)
#   console.sh stop    la ferme — seulement si c'est bien la console (fenêtre « status »)
# Ne touche jamais aux autres sessions (dig, digorch, jury-central, rpgquest…). Une session
# « lodyorch » qui n'est pas la console (ex. session Claude interactive) n'est ni tuée ni réutilisée.
set -eu
S=${LODYORCH_TMUX_SESSION:-lodyorch}
T="tmux"
is_console() { $T list-windows -t "=$S" -F '#W' 2>/dev/null | grep -qx status; }

case "${1:-}" in
start)
    if $T has-session -t "=$S" 2>/dev/null; then
        if is_console; then echo "Console $S déjà présente."; exit 0; fi
        echo "Refus : une session tmux « $S » existe déjà et n'est pas la console LODYORCH." >&2
        echo "La renommer d'abord : tmux rename-session -t $S <autre nom>" >&2
        exit 1
    fi
    $T new-session -d -x 200 -y 50 -s "$S" -n status "watch -n 20 -t lodyorch status"
    $T set-option -t "=$S" default-size 200x50
    $T set-option -t "=$S" window-size largest
    $T new-window -d -t "=$S" -n journal "journalctl -u lodyorch -f -n 50"
    $T new-window -d -t "=$S" -n events "watch -n 20 -t lodyorch log"
    $T new-window -d -t "=$S" -n activite /opt/lodyorch/console-pulse.sh
    $T select-window -t "=$S:activite"
    ;;
stop)
    if is_console; then $T kill-session -t "=$S"; else echo "Aucune console $S à fermer."; fi
    ;;
*)
    echo "usage : $0 start|stop" >&2
    exit 2
    ;;
esac
