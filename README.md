# News, Newspapers

Today's newspaper front pages from the
[Freedom Forum](https://frontpages.freedomforum.org) archive, one paper per
render, on a Tesserae panel.

![Newspaper front page in a large cell](screenshots/lg.png)

## What it does

- **Rotates through your papers.** The list lives on the widget's admin page
  (Widgets, News, Newspapers). Each render shows the next paper in the
  rotation; a cell can instead be pinned to one paper in its Paper option.
- **A paper's own CSS.** Each row has optional CSS (a few declarations such as
  `transform: translateY(-3.5%)` or `filter: contrast(1.15)`) applied to that
  paper's page image only. Anything outside a small, closed vocabulary is
  dropped.
- **Pages download in the background**, so a render never waits on the
  network. The page is the `lg` JPEG Freedom Forum publishes for each paper
  (about 700 px wide). A paper that isn't out yet shows its last good page,
  up to 30 days old, and the worker tries again with a backoff.
- **Admin page extras:** a Verify button per row (checks a paper code and
  shows a thumbnail), a Fetch now button, and a log of what the worker did.

![The admin page](screenshots/admin.png)

## Settings

No API key and no Settings > Widgets entries. The paper list and the
"On smaller screens" choice are kept in the widget's own `config.json` in its
data folder, edited on the admin page.

| Cell option | Notes |
| --- | --- |
| Paper | Empty rotates through the admin list; a paper code (for example `NY_NYT`) pins the cell to it. |

The rotation moves at most once every four minutes, so a send prepared ahead
of time and its follow-ups show one paper. Two sends inside four minutes show
the same paper twice.

## Network

Only `cdn.freedomforum.org` (the page images) and
`frontpages.freedomforum.org` (the paper list for the picker), both declared
in `plugin.json`.

## Host helpers

`app.plugin_http` (the paper list) and, when present, `app.jobs` (the
background job tray). Nothing else from the host beyond the plugin contract.

## Tests

The tests run against a checkout of Tesserae `main`. Copy this folder in as
`plugins/news_front_pages` (a copy, not a symlink: the widget takes its id from
its folder name) and run from the checkout root:

```sh
python -m pytest plugins/news_front_pages/tests
```

They never touch the network. The admin-page browser tests need Playwright's
Chromium and skip themselves without it; the client check needs `node`.

## Licence

AGPL-3.0, see [LICENSE](LICENSE).
