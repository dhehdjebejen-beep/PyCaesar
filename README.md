# PyCaesar

![License: GPL v3](https://img.shields.io/badge/license-GPL%20v3-blue.svg)

> **Language:** **English** | [Русский](README.ru.md)

> **Project status: Completed (February – November 2026).**  
> A nine-month journey of taking a pure-Python chess bot from ~2000 Elo through a full technical audit, hitting the CPython performance wall, rewriting the core in 64-bit Cython with Syzygy tablebases, and breaking **2637–2680 Elo** against Stockfish 19. Development is now officially complete and the repository is archived in its final state.

---

## The Story Behind PyCaesar

### Chapter 1. Starting in Pure Python (February – June 2026)
PyCaesar began in February 2026 as an experiment: build a complete chess engine and desktop GUI in pure Python (`python-chess` + `PyQt6`) and find out how strong a Python chess engine can really get.

By summer, the engine had alpha-beta search, transposition tables, null-move pruning, LMR, aspiration windows, a Polyglot opening book (`book.bin`), and a PyQt6 interface. It could beat human players comfortably, yet in complex positions and simple endgames it occasionally made baffling blunders — clear signs of hidden mathematical bugs underneath.

### Chapter 2. The Technical Audit & a 3-Line 13–1 Fix (August 2026)
In August, a full technical audit of the codebase was carried out (the original detailed report is preserved in [`AUDIT.md`](AUDIT.md)). It uncovered bugs that were hard to believe until verified by live tests:

- **Every Piece-Square Table (PST) was upside down for both colors.** Because vertical mirroring (`sq ^ 56`) was swapped between white and black, a white pawn on its starting square `e2` scored **+50** (**+80** in the endgame), while a pawn one step from promotion on `e7` scored **−20**. The king was penalized for castling to `g1` and rewarded for marching to `g8` into the enemy camp; rooks preferred their own 2nd rank over the 7th. Because the bug hit both sides symmetrically, the engine had been playing purely on tactics while completely misunderstanding pawns, kings, and rooks.
- **It couldn't checkmate with Queen + King vs King.** A single check extended search depth twice — once in the parent node and again in the child. At a nominal depth of 4, checking chains exploded to **ply 102**, starving the search. In elementary `KQ vs K`, the engine couldn't finish iteration 2 in 8 seconds and delivered **stalemate on move 1** while reporting `+9.23`.

A head-to-head match between the "as-is" engine and the exact same code with just **three lines fixed** (PST orientation + double check extension) ended **13.0 – 1.0** (`+12 =2 -0`, 9 wins by checkmate). `KQ vs K` went from stalemate on move 1 to checkmate in 12 moves.

All 11 audited defects were then fixed: SEE was rewritten, true MultiPV implemented, TT memory cut from 800 MB to 95 MB, `evaluate()` ported to bitboards (**156.6 → 47.7 µs**, a 3.3× speedup bit-identical across 1,765 positions), and a UCI interface (`PyCaesarUCI`) plus Elo/SPRT harnesses (`bench/`) were added.

### Chapter 3. Hitting the Pure-Python Ceiling (~2000–2100 Elo)
An attempt was made to scale across CPU cores via Python `multiprocessing` (`ProcessPoolExecutor`). An honest 10-game match of 2 workers vs 1 ended **4.5 – 5.5**: without a shared transposition table, separate processes duplicated work and lost alpha-beta cutoffs, doubling CPU usage for zero Elo gain. The idea was cleanly dropped.

Calibration matches against Stockfish (`UCI_LimitStrength`) measured the practical ceiling of pure Python + `python-chess`:
- **~5,500–10,000 nps**, depth **5.6–7.4 plies**;
- **~1864 Elo** at 0.8 s/move, **~1999 Elo** at 2.0 s/move, and **~2093 Elo** at 4.0 s/move.

Profiling made the wall unmistakable: ~80% of node time went into `evaluate()` and `python-chess` object overhead. Algorithmic tuning alone could no longer move the needle — the foundation had to change.

### Chapter 4. Leaving `python-chess` in Search, Cython & Breaking 2600+ (Autumn – November 2026)
For the final chapter, the engine core was rewritten from scratch:
1. **Custom `FastBoard` core** ([`fast_board.py`](recovered_src/engine/fast_board.py) + [`fast_eval.py`](recovered_src/engine/fast_eval.py)) — zero `chess.Board` / `chess.Move` allocations inside the search tree, packed integer moves, incremental evaluation. Even in pure Python, throughput jumped to **33–84k nps**.
2. **Compiled 64-bit Cython core** ([`_fast_core.pyx`](recovered_src/engine/_fast_core.pyx)) — `uint64_t` bitboards, hardware `POPCNT`/`TZCNT`, flat 16-byte C transposition table entries, a 65,536-entry C pawn hash, and incremental PeSTO evaluation. Automatically falls back to `FastBoard` if the binary extension is not compiled.
3. **Custom positional style** — reinforced pawn shield (`1.5×`) and pawn structure (`1.3×`), knight outposts, rook on the 7th rank, opening discipline, safe mobility, pawn threats, free passed pawns, bishop pair, and endgame mop-up.
4. **Built-in Syzygy tablebases (3–4 pieces)** — 70 `.rtbw`/`.rtbz` files bundled directly in the repository and executables, with DTZ root probing ([`tablebase.py`](recovered_src/engine/tablebase.py)).

The leap was dramatic: **~255× faster** (**2.55–4.3M nps**), average depth past **15 plies**, and playing strength jumping from ~2000 to **2637–2680 Elo** against Stockfish 19 — surpassing the original goal ("2500 Elo is more than enough").

With that milestone reached in **November 2026**, PyCaesar is complete.

---

## Evolution in Numbers

| Stage | Core | Speed | Depth | Measured Elo (Stockfish) |
|---|---|---:|---:|---:|
| Pre-audit (June 2026) | Python + `python-chess` (C1/C2 bugs) | ~4k nps | 1–4 | stalemated `KQ vs K` |
| Pure Python ceiling (Aug 2026) | Python + bitboard `evaluate()` | ~5.5–10k nps | 5.6–7.4 | **~1999** (2s) / **~2093** (4s) |
| Pure Python rewrite | `FastBoard` (no `python-chess` in search) | 33–84k nps | 8–10 | built-in fallback |
| **Final (Nov 2026)** | **Cython `_fast_core.pyx` + Syzygy** | **2.55–4.3M nps** | **15.8** | **2637** (1.2s) / **~2680** (2s) |

---

## Strength Calibration (All 10 Levels)

Tested against **Stockfish 19** (`1.2 s/move`, 16 paired games per level = 8 openings × 2 colors). Levels **6–10** play with zero artificial noise; levels **1–5** cap depth/quiescence and add controlled noise.

| Level | Mode | Opponent | Score | Win % | Measured Elo |
|---:|---|---|---:|---:|---:|
| **10** | Full strength (no noise) | **Stockfish 19 (2500)** | `+10 =2 -4` | **68.8%** | **2637** (±196) |
| **9** | Max depth 10 | Stockfish 19 (2350) | `+12 =3 -1` | 84.4% | **~2520** |
| **8** | Max depth 8 | Stockfish 19 (2300) | `+8 =3 -5` | 59.4% | **2366** (±169) |
| **7** | Max depth 6 | Stockfish 19 (2150) | `+6 =2 -8` | 43.8% | **2106** (±174) |
| **6** | Max depth 5 | Stockfish 19 (1950) | `+10 =2 -4` | 68.8% | **2087** (±196) |
| **5** | Max depth 4 (noise 30) | Stockfish 19 (1800) | `+4 =2 -10` | 31.2% | **1663** (±196) |
| **4** | Max depth 3 (noise 60) | Stockfish 19 (1600) | `+3 =1 -12` | 21.9% | **1379** (±285) |
| **3** | Max depth 2 (noise 100) | Stockfish 19 (1320) | `+5 =2 -9` | 37.5% | **1231** (±182) |
| **2** | Max depth 2 (qs=1, noise 150) | PyCaesar L3 (1231) | `+1 =0 -15` | 6.2% | **761** (±268) |
| **1** | Max depth 1 (qs=0, noise 220) | PyCaesar L2 (761) | `+0 =0 -16` | 0.0% | **≈ 400** |

Additional match vs **Stockfish 19 (2400 Elo, 2.0 s/move, 6 games)**: **`+4 =2 -0` (83.3% → ~2680 Elo)**, average depth **15.78**, speed **2.55M nps**.

---

## Highlights

- **Search:** Fail-soft Negamax, PVS, Aspiration Windows, flat C TT, Null-Move Pruning, RFP, Razoring, Futility Pruning (check-safe), LMP, IIR, LMR, Quiescence + SEE, Killers, Counter-Move, History / Capture History, true MultiPV, Polyglot book (`book.bin`), bundled 3–4 piece Syzygy tablebases.
- **Desktop GUI ([`recovered_src/gui/`](recovered_src/gui/)):** PyQt6 interface with Human vs Bot and Human vs Human modes (optional full-strength 2-arrow hints or completely disabled engine), live evaluation/depth updates, post-game analysis, FEN import/export, undo/redo.
- **UCI ([`recovered_src/uci.py`](recovered_src/uci.py)):** Supports `Hash`, `Skill Level`, `UCI_LimitStrength`, `UCI_Elo` (400–2637), `OwnBook`, and `SyzygyPath`.
- **Audit & Tests:** Full historical audit in [`AUDIT.md`](AUDIT.md) and 22 automated tests in [`tests/`](tests/) (Perft, evaluation antisymmetry, SEE, MultiPV, Syzygy, TT mate encoding, and UCI end-to-end).

---

## Quick Start & Build

```bash
pip install -r requirements.txt
python setup_cython.py                      # Compile the Cython core (requires a C compiler)
python recovered_src/main.py                # Launch PyQt6 GUI
python recovered_src/uci.py                 # Run UCI engine
python -m unittest discover -s tests -v     # Run 22 unit tests
python tools/download_syzygy.py --pieces 5  # Optional: download 5-piece Syzygy (~938 MB)
```

On Windows, run [`build_windows.bat`](build_windows.bat) to build standalone `PyCaesar.exe` and `PyCaesarUCI.exe` into `dist\`.

---

## License

Copyright (c) 2026 [@dhehdjebejen-beep](https://github.com/dhehdjebejen-beep).  
Licensed under the **[GNU General Public License v3.0](LICENSE)** (`GPL-3.0-only`), matching the licenses of its core dependencies (`PyQt6` and `python-chess`). Any derivative works or distributions must preserve copyright notices and remain open source under GPL v3.0.

## Author

[@dhehdjebejen-beep](https://github.com/dhehdjebejen-beep)
