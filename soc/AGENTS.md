# soc/

One directory per SoC: `soc/<vendor>/<soc>/{soc.json,shim/,docs/}`. `soc.json` is the single source of constants
(register bases, GIC, timer, UFS blocks); `tools/boards.py` generates the headers from it.

- Add or change a constant in `soc.json` and cite its source in `docs/` (Linux driver + commit, ABL decompile, dump).
- Mark each fact: measured on hardware, read from source, or unverified.
- `tools/pix check` must pass; generated headers for existing boards must stay byte-identical unless the change is
  the point of the commit.

- Coding rules: `docs/CODING-RULES.md` (CR-xx ids; each names its check). Read the rules for this area before editing; `tools/check-rules.py` enforces the lint ones in the gate.
