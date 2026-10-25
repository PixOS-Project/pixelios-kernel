"""R22 (CR-22): a nonzero write to an *IRQ_MASK register needs its interrupt handler in the same patch file (nothing else unmasks-and-services it:
one stuck level interrupt hangs the only CPU, review gpu-mali-reset-review-1 M2). Added `+` lines outside comments only.
Handler names are XNU APIs seen in the pristine tree (observed: grep xnu-pristine): ml_install_interrupt_handler, PE_install_interrupt_handler,
registerInterrupt, IOInterruptEventSource, IOFilterInterruptEventSource. Spec: docs/CODING-RULES.md CR-22."""
import re
from .lib import Finding, tracked, plus_lines, strip_c_comments

RULE = "R22"
WRITE_RX = re.compile(r"\b\w*(?:wr|write)\w*\s*\(\s*\w*IRQ_MASK\w*\s*,\s*([^;]*?)\)\s*;")
ZERO_RX = re.compile(r"\(?\s*0[xX]?0*[uUlL]*\s*\)?")
HANDLER_RX = re.compile(r"\b(?:ml_install_interrupt_handler|PE_install_interrupt_handler|registerInterrupt|IOInterruptEventSource|IOFilterInterruptEventSource)\b")


def scan(lines, handler_rx=HANDLER_RX):
    added = [(n, strip_c_comments(t)) for n, t in plus_lines(lines)]
    if any(handler_rx.search(c) for _, c in added):
        return []
    return [n for n, c in added for m in [WRITE_RX.search(c)] if m and not ZERO_RX.fullmatch(m.group(1).strip())]


def check(root, handler_rx=HANDLER_RX):
    out = []
    for rel, lines in tracked(root, "xnu/patches/*.patch"):
        for n in scan(lines, handler_rx):
            out.append(Finding(rel, n, lines[n - 1], f"{RULE} nonzero-irq-mask: nonzero IRQ_MASK write with no interrupt handler in this patch (keep the mask 0 and poll)"))
    return out
