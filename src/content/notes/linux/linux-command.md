---
title: "A calmer way to learn Linux commands"
description: "The small mental models that make the shell feel less like a list of incantations."
date: 2025-12-08
category: "Linux"
tags: ["shell", "cli", "systems"]
---

The fastest way to learn a command is to give it a job that matters. Instead of memorising flags in isolation, start with the shape of the problem: inspect, transform, then compose.

## Three questions before a command

When I get stuck in a terminal, I ask:

1. What is the input — a file, a stream, or a process?
2. What should the output look like?
3. Can I make the next step visible before I make it destructive?

That last question is why `printf` and `less` are such good friends. They make an intermediate result inspectable.

```bash
find . -type f -name '*.md' -print0 \
  | xargs -0 grep -n "TODO" \
  | less
```

## Compose small tools

Unix commands become more useful when each one does one legible thing. `find` locates, `grep` selects, and `less` gives me a pause button. The exact flags matter less once the flow is clear.

I keep the commands I actually use in the [notes](../..) section of this site, with the context that made them stick.
