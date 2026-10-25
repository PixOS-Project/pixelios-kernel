# AI-driven development

**PixOS is developed mostly by AI coding agents, directed by a human maintainer.** We say so
up front because it changes how you should read this repository.

## How the project is built

- **Agents write the code and the docs.** The shim, the XNU patches, the QR log channel, the
  research notes and most tooling were written by large language model agents (mainly Anthropic's
  Claude models; other models were used for delegated subtasks such as reading Linux driver sources or
  drafting tests).
- **A human sets direction and holds the keys.** The maintainer chooses goals, owns the phone, and
  reads the results. Agents run the host tools and, through scripted tools only (`tools/xrun`,
  `tools/fboot.sh`, `tools/phone`), boot images with `fastboot boot`, which writes nothing; flashing the
  `userdata` partition with the iOS root is pre-approved by the maintainer. Anything else that writes
  to a phone (any other partition, relocking the bootloader, switching boot slots) needs the maintainer's
  explicit confirmation, and so does publishing; see [Safety](SAFETY.md). Some work is done by agents
  while the maintainer is away, inside those limits.
- **Independent review.** Code that touches hardware gets a separate read-only review by a second
  agent session, written up in [Reviews](reviews/hs-g4-review-1.md). Those reviews found real bugs
  (write paths, DMA assumptions, unsound audit rules). The reviewers are also AI, so a review is
  a second opinion, not a proof.
- **Hardware is the judge.** Nothing is called working until it was observed on a real Pixel 6a.
  [Status](https://github.com/PixOS-Project/pixelios-site/blob/main/docs/STATUS.md) separates what was observed on the device from what only builds or is guessed.
- **Automatic checks.** Host tests, a build-time audit that rejects any storage write path in
  pre-kernel code (`tools/test-bluejay-boot.py`), and a check that the published repositories rebuild the
  private working tree (scan for private data, reconstruction, no dangling links).
- **Attribution.** Commits made by an agent end with a `Co-Authored-By:` trailer naming the model.

## What that means for you

- **Expect mistakes.** Generated code can be wrong in confident ways. Read it before you trust it,
  especially anything that writes to hardware.
- **Claims are evidence-tagged.** "Works" means it was seen on a device, with a commit or log to point at.
  "Builds" and "todo" mean exactly that. Guesses are labelled as guesses.
- **Use a spare phone.** This code runs at the highest privilege level on an unlocked device.
- **The research notes cite primary sources** (kernel source files, register names) so claims can be
  checked rather than taken on faith. Where a source could not be verified, the note says so.

## Contributing with AI assistance

AI-assisted contributions are welcome here, as they are how the project works. The rules are the
same for everyone:

1. You are responsible for what you submit. Read it, build it, run the tests.
2. Say which parts were AI-generated if that is not obvious, and what you tested on hardware.
3. Do not paste secrets, device serials, partition dumps or Apple firmware into prompts or commits.
4. Code that writes to storage or hardware registers needs a human reading it line by line
   and, ideally, an independent review ([CONTRIBUTING](../CONTRIBUTING.md)).
