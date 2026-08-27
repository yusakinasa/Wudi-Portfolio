---
title: "A content model that stays out of the way"
description: "How a small Astro site can keep content in files while pages remain composable."
date: 2025-07-14
category: "Frontend"
tags: ["astro", "content", "typescript"]
---

The most useful abstraction in a personal site is often a good folder boundary. Pages know how to present a note; Markdown knows what the note says.

## Keep the contract small

Each note in this site has a title, description, date, category, and tags. The schema lives in `src/content.config.ts`, so malformed frontmatter fails during the build instead of becoming a mysterious empty card.

The page only asks the collection for published notes, sorts them by date, and passes each entry to a small presentational component. Adding a note does not require editing a route or a component.

## Let the filesystem be the CMS

Markdown files are easy to search, review, and move. A nested path such as `linux/linux-command.md` becomes the stable URL `/notes/linux/linux-command/`, while the frontmatter remains readable in a pull request.
