<p align="center">
  <img src="assets/branding/odysseus-wordmark.png" alt="Odysseus" width="238">
</p>

<h1 align="center">Odysseus Lite</h1>

<p align="center">
  <strong>Odysseus Lite</strong> — a local-first AI harness that helps small language models do more with tools, memory, and a full workspace, including on constrained hardware.
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> ·
  <a href="website/setup.md">Setup Guide</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="ROADMAP.md">Roadmap</a>
</p>

<p align="center">
  <img src="assets/branding/odysseus-browser.jpg" alt="Odysseus interface">
</p>

---

## Quick Start

```bash
git clone https://github.com/hpvaneeden/Odysseus-lite.git
cd Odysseus-lite
cp .env.example .env
docker compose up -d --build
```

Open `http://localhost:7000` when the containers are healthy. The first admin password is printed in `docker compose logs odysseus`.

Native installs, GPU notes, Windows/macOS instructions, HTTPS, and configuration live in the [setup guide](website/setup.md).

## Features

- **Chat + Agents** — local/API models, tools, MCP, files, shell, skills, and memory.
- **Cookbook** — hardware-aware model recommendations, downloads, and serving.
- **Deep Research** — multi-step web research with source reading and report generation.
- **Compare** — blind side-by-side model testing and synthesis.
- **Documents** — writing-first editor with AI edits, suggestions, Markdown, HTML, CSV, and syntax highlighting.
- **Email** — IMAP/SMTP inbox with triage, tags, summaries, reminders, and reply drafts.
- **Notes, Tasks + Calendar** — reminders, todos, scheduled agent tasks, and CalDAV sync.
- **Extras** — gallery/image editor, themes, uploads, web search, presets, sessions, and 2FA.

### Built for small models

Odysseus Lite keeps the model replaceable and gives it a capable harness around it. The focus is making local and compact models more useful on modest hardware:

- **Evidence-based continuity** — owner-scoped memory and interaction feedback can carry useful context across chats, with privacy gates and reset controls.
- **Opt-in adaptation** — decaying response guidance, explicit user emotion reports, and simulated task affect are separate layers; the system does not treat a mood label as a diagnosis.
- **Reflection and inspection** — repeated interaction evidence can inform traits, while an admin-only Brain insights panel shows stored signals and state without copying chat transcripts into the dashboard.
- **Voice interface** — browser-based speech input/output, a configurable dot-matrix face, and themes for a more direct voice interaction.
- **Tools around the model** — MCP, web search, memory, and workspace tools let smaller models act through the harness instead of relying on model size alone.

## Why I'm building this

I started Odysseus Lite as a hobby project because I want small local models to be useful collaborators, even on constrained hardware. The model is the engine—or soul; the harness is its body and neural network; tools are its limbs. I’m exploring how far that idea can go when memory, tools, and a personality that develops over time work together.

## Origins and attribution

Odysseus Lite is an independent hobby project built from and inspired by the original [Odysseus project](https://github.com/odysseus-dev/odysseus). The original Odysseus developers created the foundation and deserve clear credit for their work. This project builds on that foundation with a particular focus on small-model support, evolving interaction, and constrained-hardware use; it is not an official Odysseus release. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for third-party code, assets, and license notices.

## Contributing

Help is welcome. The best entry points are fresh-install testing, provider setup bugs, mobile/editor polish, docs, and small focused refactors. See [CONTRIBUTING.md](CONTRIBUTING.md) and [ROADMAP.md](ROADMAP.md).

## Security

Odysseus Lite is a self-hosted workspace with powerful local tools. Keep auth enabled, keep private data out of Git, and do not expose raw model/service ports publicly.

- Keep `AUTH_ENABLED=true` for any network-accessible deployment.
- Keep `LOCALHOST_BYPASS=false` outside local development.

Deployment details are in the [setup guide](website/setup.md#security-notes).

## Star History

<a href="https://star-history.dera.page/#hpvaneeden/Odysseus-lite&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=hpvaneeden/Odysseus-lite&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=hpvaneeden/Odysseus-lite&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=hpvaneeden/Odysseus-lite&type=date&legend=top-left" />
 </picture>
</a>

## License

AGPL-3.0-or-later -- see [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
