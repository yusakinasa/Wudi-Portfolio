---
title: "Prompting as an interface design problem"
description: "A practical note on making AI instructions observable, scoped, and easier to revise."
date: 2025-10-21
category: "AI"
tags: ["llm", "writing", "workflow"]
---

Good prompts are less like magic spells and more like small interfaces. They expose the useful controls, define a sensible default, and make the result easy to inspect.

## Start with the boundary

Before adding detail, state what the model should not decide. A tight boundary reduces the number of plausible-but-wrong directions and leaves room for the useful judgement to happen.

I usually write a prompt in three layers:

- **Context:** what already exists and who it is for.
- **Task:** one concrete transformation or decision.
- **Check:** how I will recognise a good result.

The check is the part I used to omit. It turns a vague request into something I can iterate on.

## Keep an observable trail

For recurring work, save the input, the output, and one sentence about what changed. This creates a tiny evaluation set and makes prompting feel closer to debugging than guessing.
