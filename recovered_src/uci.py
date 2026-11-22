"""
Required Notice: Copyright (c) 2026 dhehdjebejen-beep (https://github.com/dhehdjebejen-beep)
License: PolyForm Noncommercial License 1.0.0 (https://polyformproject.org/licenses/noncommercial/1.0.0)

uci.py — UCI-интерфейс PyCaesar

Запуск из исходников:
    python recovered_src/uci.py

Поддерживается:
    uci, isready, ucinewgame, quit
    position [startpos | fen <fen>] [moves ...]
    go [wtime btime winc binc movestogo movetime depth nodes infinite]
    stop, ponderhit (как stop)
    setoption name <Hash|Skill Level|UCI_LimitStrength|UCI_Elo|OwnBook|SyzygyPath> value <v>
"""

import io
import os
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdin.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chess

from engine.engine import ChessEngine
from engine.search import MATE_SCORE
from engine import strength as strength_levels

ENGINE_NAME = "PyCaesar"
ENGINE_AUTHOR = "ГЛАВНЫЙ_БОСС"


def _uci_score(score: int) -> str:
    """Оценка в формате UCI: mate в ходах либо cp."""
    if abs(score) >= MATE_SCORE:
        plies = 99999 - abs(score)
        moves = (plies + 1) // 2
        return f"mate {moves if score > 0 else -moves}"
    return f"cp {score}"


class UciEngine:
    def __init__(self) -> None:
        self.engine = ChessEngine()
        self.board = chess.Board()
        self.own_book = False
        self.limit_strength = False
        self.requested_elo = 2150
        self.skill_level = strength_levels.FULL_STRENGTH
        self._search_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _send(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def cmd_uci(self) -> None:
        self._send(f"id name {ENGINE_NAME}")
        self._send(f"id author {ENGINE_AUTHOR}")
        self._send("option name Hash type spin default 95 min 4 max 1024")
        self._send(f"option name Skill Level type spin default {strength_levels.MAX_LEVEL}"
                   f" min 1 max {strength_levels.MAX_LEVEL}")
        self._send("option name UCI_LimitStrength type check default false")
        self._send("option name UCI_Elo type spin default 2637 min 400 max 2637")
        self._send("option name OwnBook type check default false")
        default_tb = self.engine.tablebase.path or "<empty>"
        self._send(f"option name SyzygyPath type string default {default_tb}")
        self._send("uciok")

    def cmd_setoption(self, tokens: list) -> None:
        if "name" not in tokens:
            return
        name_idx = tokens.index("name")
        value_idx = tokens.index("value") if "value" in tokens else len(tokens)
        name = " ".join(tokens[name_idx + 1:value_idx]).strip()
        value = " ".join(tokens[value_idx + 1:]).strip() if value_idx < len(tokens) else ""
        low = name.lower()

        if low == "hash":
            try:
                self.engine.search_engine.tt.set_hash_mb(int(value))
                if self.engine.search_engine._cy is not None:
                    self.engine.search_engine._cy.set_hash_mb(int(value))
            except ValueError:
                pass
        elif low == "skill level":
            try:
                self.skill_level = int(value)
                if not self.limit_strength:
                    self.engine.set_strength(self.skill_level)
            except ValueError:
                pass
        elif low == "uci_limitstrength":
            self.limit_strength = value.lower() == "true"
            self._apply_strength()
        elif low == "uci_elo":
            try:
                self.requested_elo = int(value)
            except ValueError:
                return
            self._apply_strength()
        elif low == "ownbook":
            self.own_book = value.lower() == "true"
        elif low == "syzygypath":
            self.engine.set_syzygy_path(value)

    def _apply_strength(self) -> None:
        if self.limit_strength:
            self.engine.set_strength(strength_levels.level_for_elo(self.requested_elo))
        else:
            self.engine.set_strength(self.skill_level)

    def cmd_position(self, tokens: list) -> None:
        if len(tokens) < 2:
            return
        if tokens[1] == "startpos":
            self.board = chess.Board()
            rest = tokens[2:]
        elif tokens[1] == "fen":
            fen_parts = []
            i = 2
            while i < len(tokens) and tokens[i] != "moves":
                fen_parts.append(tokens[i])
                i += 1
            try:
                self.board = chess.Board(" ".join(fen_parts))
            except ValueError:
                return
            rest = tokens[i:]
        else:
            return

        if rest and rest[0] == "moves":
            for uci in rest[1:]:
                try:
                    move = chess.Move.from_uci(uci)
                except ValueError:
                    break
                if move not in self.board.legal_moves:
                    break
                self.board.push(move)

    def _budget(self, params: dict) -> tuple[float, int]:
        max_depth = int(params.get("depth", 64))
        if ("depth" in params or "nodes" in params) and not (
                params.keys() & {"movetime", "wtime", "btime"}):
            return 3600.0, max_depth
        if "movetime" in params:
            return max(0.01, params["movetime"] / 1000.0), max_depth
        if params.get("infinite"):
            return 3600.0, max_depth
        white = self.board.turn == chess.WHITE
        remaining = params.get("wtime" if white else "btime")
        if remaining is None:
            return 3.0, max_depth
        inc = params.get("winc" if white else "binc", 0) / 1000.0
        moves_to_go = params.get("movestogo", 30)
        remaining_s = remaining / 1000.0
        budget = remaining_s / max(1, moves_to_go) + inc * 0.8
        reserve = min(0.3, remaining_s * 0.05)
        budget = min(budget, max(0.05, remaining_s - reserve) * 0.3)
        return max(0.05, budget), max_depth

    def cmd_go(self, tokens: list) -> None:
        params: dict = {}
        i = 1
        numeric = {"wtime", "btime", "winc", "binc", "movestogo", "movetime", "depth", "nodes", "mate"}
        while i < len(tokens):
            tok = tokens[i]
            if tok in numeric and i + 1 < len(tokens):
                try:
                    params[tok] = int(tokens[i + 1])
                except ValueError:
                    pass
                i += 2
                continue
            if tok == "infinite":
                params["infinite"] = True
            i += 1

        self.stop_search()
        budget, max_depth = self._budget(params)
        board = self.board.copy()

        def run() -> None:
            def on_iteration(info: dict) -> None:
                elapsed = max(info["time"], 1e-3)
                nps = int(info["nodes"] / elapsed)
                pv = " ".join(m.uci() for m, _ in info["multi_pv"][:1])
                self._send(
                    f"info depth {info['depth']} score {_uci_score(info['score'])} "
                    f"nodes {info['nodes']} nps {nps} "
                    f"time {int(elapsed * 1000)} pv {pv}"
                )

            try:
                lines, score, depth = self.engine.get_best_move(
                    board, time_limit=budget, use_book=self.own_book,
                    on_iteration=on_iteration, max_depth=max_depth)
            except Exception:
                import traceback
                traceback.print_exc(file=sys.stderr)
                lines = []

            if lines:
                self._send(f"bestmove {lines[0][0].uci()}")
            else:
                self._send("bestmove 0000")

        with self._lock:
            self._search_thread = threading.Thread(target=run, daemon=True)
            self._search_thread.start()

    def stop_search(self) -> None:
        with self._lock:
            thread = self._search_thread
        if thread is not None and thread.is_alive():
            self.engine.stop()
            thread.join(timeout=10.0)
        with self._lock:
            self._search_thread = None

    def run(self) -> None:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            tokens = line.split()
            cmd = tokens[0]

            if cmd == "uci":
                self.cmd_uci()
            elif cmd == "isready":
                self._send("readyok")
            elif cmd == "setoption":
                self.cmd_setoption(tokens)
            elif cmd == "ucinewgame":
                self.stop_search()
                self.engine.clear_cache()
                self.board = chess.Board()
            elif cmd == "position":
                self.cmd_position(tokens)
            elif cmd == "go":
                self.cmd_go(tokens)
            elif cmd in ("stop", "ponderhit"):
                self.stop_search()
            elif cmd == "quit":
                self.stop_search()
                self.engine.close()
                return
            elif cmd == "d":
                self._send(str(self.board))
                self._send(f"Fen: {self.board.fen()}")


def main() -> None:
    UciEngine().run()


if __name__ == "__main__":
    main()
