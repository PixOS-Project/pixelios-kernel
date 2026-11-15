# Starting a HackiOS port with an LLM

How to start bringing up Apple's open-source XNU kernel and an iOS userland on hardware that was never meant to run it, with an LLM agent doing most of the typing and you holding the phone.

**Read this first.** PixOS has only been verified on one device, the Pixel 6a (bluejay, Tensor G1). Everything about other devices here is what we expect, not what we have seen. Tags: *observed* (seen on our hardware), *read* (read in the source), *guess*. Tool results were checked on commit 17a3b5d4, 2026-11-07.

## Newbies quickstart

1. Know how to get your device back to stock **before anything else**: a factory image, and a button combination that reaches the bootloader even when the software is hung. If you cannot name both, stop here.
2. Clone [pixelios-kernel](https://github.com/PixOS-Project/pixelios-kernel) and open your agent (Claude Code, Cursor, Codex, OpenCode, ...) in it.
3. Paste the prompt below as your first message, or into `AGENTS.md` / `CLAUDE.md` / `.cursorrules`. Fill in the first line.

```text
You are helping me port Apple's XNU kernel and an iOS userland to:
DEVICE: <brand, model, codename>   SOC: <chip>   BOOT: <fastboot / U-Boot / UEFI / unknown>
Reference port: PixOS (Pixel 6a). Read docs/HACKIOS-GUIDE.md, docs/SAFETY.md and docs/STYLE-C.md first.

SAFETY
- RAM boot only: `fastboot boot` or your bootloader's load-to-RAM command. It writes nothing to storage.
- Never write bootloader, recovery, boot, vbmeta, dtbo, modem, persist, efs or the partition table. Never relock.
- Before any hardware command, check the connected device reports the expected codename. Any other device is never touched.
- I run the hardware. Give me the exact command and what to expect, then wait for my result.
- Replacing the data partition with the iOS root erases it: only with my explicit yes, after recovery has been shown to work.
- No secrets, serial numbers, firmware or Apple binaries in git. I supply those locally.

BRINGUP ORDER. Do not start a stage before the previous one is shown working on the hardware.
0. Recovery: a hung boot returns to the bootloader by a known route.
1. Witness: any output that survives a hang (UART, screen, RAM log kept across reset, USB gadget).
2. Timer and interrupts: counter frequency, interrupt controller, one timer tick seen in the log.
3. MMIO and memory map: memory carve-outs and peripheral addresses, cited from the device tree or vendor source.
4. XNU boots, then a minimal userland (launchd, one test binary).
5. One block at a time: storage, display, touch, then GPU and radios.

EVIDENCE
- Done means shown. Quote the log line, register value or test RESULT line, with date and build. If you have none, say "not verified".
- Tag every claim: observed, read from source (file:line), or guess. Never invent a register, offset or flag. Find the answer in this order: Linux/BSD/vendor source, decompile of the vendor binary, a capture from the stock OS, and only then a boot of our code.
- Never fake an oracle: no simulator that accepts anything, no canned output, no test that cannot fail. Never delete or loosen a test or a safety check to get a pass; fix the code.
- No silent failures: every stub, ignored value or error shows in the log.

BOOTS. Before each boot write down the question, the predicted log lines, and what each outcome means. Change one thing per boot against a known baseline, or batch several read-only checks. After two boots with no explained result, stop and revisit your model of the hardware.

C ON THE DEVICE (docs/STYLE-C.md): every loop and hardware poll has a fixed bound and a timeout that logs what it waited for; no recursion; static allocation only; sized types (uint32_t) with the unit last in the name (timeout_us_max); check pre- and postconditions with an assert that logs and takes a controlled stop; handle and log every error; at most 70 lines per function and 100 columns.

GATES decide, not opinions: compiler warnings as errors, sanitizers on host tests, linters, and tests that are shown to fail when the code is broken. Your own "looks good" counts for nothing. If I ask for a review, report only what you can prove with a failing command or a cited source. "No findings" is a valid answer.

WORKING STYLE: keep to the task and name what you will not touch. Commit small with what was proven. Keep state in git and short notes, not in this chat. Stop and ask at a real contradiction or any data-loss risk.
```

4. Ask for stage 0 and 1 only. A first session that ends with one trustworthy log line is a good session.

## How the port is shaped

```text
iOS userland            unmodified as far as possible; you supply it (pixelios-userland builds the root)
XNU + numbered patches  xnu/patches/, never edit the fetched tree
shim                    small bare-metal loader, started by a RAM boot; brings up what XNU needs, builds its device tree
soc/ + device/          data: what a chip shares (soc.json) and what one device adds (board.json)
```

*Observed on the Pixel 6a:* the shim boots with `fastboot boot`, XNU mounts an iOS root from `userdata`, and the phone reaches the lock screen. Full evidence per item is in the [README](../README.md). The Wi-Fi and GPU work is still in progress.

## Which tools you can use

| Tool | What it does | On another target |
|---|---|---|
| `tools/pix new-device`, `pix check` | Scaffold a device and SoC tree and check it (*observed*) | Usable now. `check` requires GIC, framebuffer and **UFS** keys, so a board that boots from eMMC, SD or NVMe needs that rule and a storage driver changed (*read*) |
| `tools/change-class` | Classifies a diff as log-only, log-read, hw-write or tools, failing closed, so a log line skips heavy review (self-test 307 cases, *observed*) | Usable; path rules assume `shim/ soc/ device/` |
| `tools/lint-c --style` | Enforces the C rules on new or changed code only (*read*) | Usable; needs a merge-base branch |
| `tools/trace-diff`, `tools/trace2seq.py` | ftrace capture to register CSV; first divergence against a run; CSV to **draft** spec rows, each tagged `guess` for a human to cite (18 tests, *observed*) | Usable if you can capture ftrace under your stock OS. `trace2seq` only drafts: it never produces tables you can boot |
| `tools/public-push-guard` | Scans diffs and trees for private addresses, paths, keys and `.env` values (*observed*) | Usable; replace the host words in `tools/public-push-guard.rules` |
| `tools/test_*_mutants.py` (16 files) | Break the code on purpose and require the tests to fail (*read*) | A pattern to copy; each suite is specific to its code |
| `tools/xrun` | One hardware cycle to one report: run-plan check, build, RAM boot, log capture, verdict (*observed on bluejay*) | **Pixel-bound.** It goes through `tools/fboot.sh`, which refuses anything but `bluejay`, and assumes the phone lock and our log channel. Copy the idea; porting it is real work |
| `tools/fboot-android`, `tools/golden-pull` | Boot stock Android from RAM and pull a golden trace (*read*) | **Pixel-bound**; same idea with your own paths |
| `tools/devd.c`, `tools/devsh` | Push and run userland binaries without a reboot (*read*) | Needs XNU already up, a USB network (`ue0`, 192.168.7.x) and our kernel overlay patches, so it comes late. We publish no speed figure: no measurement is on record |

Same SoC as a port that already exists? Follow [Adding a device](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/PORTING.md) instead of starting from scratch.

**Not a phone (Raspberry Pi, RK3588, other boards):** nobody here has tried one. Expect the shim's probe sequence to need rewriting, since it is one Tensor-specific block today, and storage and interrupt code to differ. A serial port makes stage 1 much easier. Use your bootloader's load-to-RAM command in place of `fastboot boot` (*guess*). Without a reliable hardware reset or watchdog, keep a power switch within reach.

## Stage notes

**No UART?** That was our case. *Observed on the Pixel:* the shim logs to the screen, to RAM and over a USB serial gadget; under XNU the log streams over a USB network gadget as UDP; a [QR code on the screen](QR_LOG.md) read by a camera is the last resort. Build the witness before anything you want to debug.

**Recovery.** We disable the watchdogs during bring-up, so a hang stays a hang until the shim's own reset timer or a held button. Prove that route on your device early.

**Facts come from sources.** On Android, the device tree usually gives you the interrupt controller, UART and reserved memory; `dtc -I dtb -O dts` makes it readable (*guess* for your device). The vendor kernel source is often the best reference for everything else.

## Porting one hardware block

Every block goes through four stages, in order. Each ends with an artifact.

| Stage | Artifact | Done when |
|---|---|---|
| S0 Oracle | A register trace of the block working under the stock OS, kept out of git | The capture exists, with a build label |
| S1 Spec | The complete ordered sequence from power-on to the feature | Every step is cited, every unknown is listed |
| S2 Conformance | For iOS-facing parts: each selector, property or ioctl iOS asks for, marked implemented, partial or missing | Missing rows log a loud gap line |
| S3 Build and runs | Patches that implement whole spec sections, each boot with a written plan | The predicted lines appear on the hardware |

Why it exists: our GPU and Wi-Fi work once took one phone run per guess.

## Using agents: what the evidence supports

The prompt's rules come from our mistakes and from studies with published data:

- **Keep one writer.** In [CooperBench](https://arxiv.org/abs/2601.13295) (652 tasks) agents working in pairs succeeded about 30% less often than the same tasks done individually. In [real merges of agent PRs](https://arxiv.org/abs/2607.04697), pairs from different agents conflicted 41.7% of the time, against 19.8% within one agent. Use parallel agents for reading, or for paths that do not overlap.
- **Don't add agents by default.** In a [50-app benchmark](https://www.vals.ai/blogs/multi-agent-vibe-code-bench), teams cost 1.8 to 5.1 times as much and gained significantly in only one of four setups. Debate between agents [does not reliably beat](https://arxiv.org/abs/2311.17371) simpler prompting.
- **Frame reviews neutrally, and demand proof.** In [a study of 14,910 review queries](https://arxiv.org/abs/2603.18740), telling a model that code was bug-free cut one model's detection from 97.2% to 3.6%, and neutral prompts still produced mostly false alarms. So never tell a reviewer the code is fine, and accept only findings it can demonstrate. Our own reviews have caught real bugs, so a proof-carrying second read is still worth it for code that writes hardware.
- **Verify by running.** [Inadequate verification and stopping early](https://arxiv.org/abs/2503.13657) are a named failure group across 1,642 agent traces. In one of our incidents an agent wrote a simulator that overwrote mismatched registers so its tests could not fail, reporting a "ping" that was canned output. Hardware and tests that can fail are the judge.
- **Measure before you believe.** Developers in [a randomized trial](https://arxiv.org/abs/2507.09089) took 19% longer with AI while believing they were 20% faster. Pick a number for each stream, keep a baseline run, and see whether it moved.

## More

[Safety](SAFETY.md) · [C style](STYLE-C.md) · [Coding rules](CODING-RULES.md) · [Automation and xrun](AUTOMATION.md) · [How this project uses AI](AI_DEVELOPMENT.md) · [Wi-Fi firmware licensing](WIFI-LICENSING.md)

Sharing a port? Say what you have only guessed, and do not publish firmware or Apple binaries.
